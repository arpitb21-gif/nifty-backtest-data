"""
C2b — Load daily F&O bhavcopy (1,588 zip files) into daily_futures / daily_options.

ASSUMPTION (logged): only FUTIDX and OPTIDX rows for symbol in (NIFTY, BANKNIFTY)
are loaded — stock futures/options and other indices (FINNIFTY, MIDCPNIFTY,
NIFTYNXT50) are skipped. This matches our locked scope (index-only, Nifty +
BankNifty). Skipping them keeps the DB small and load time reasonable.

ASSUMPTION (logged): bhavcopy file format changed partway through the range
(older files: "fo01JUN2022bhav.csv.zip" with columns
INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,
CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP;
newer files: "BhavCopy_NSE_FO_..._20260910_F_0000.csv.zip" — NSE's new UDiFF
format with different column names). Both are handled by the loader via a
column-name mapping, detected per file.
"""
import sqlite3, zipfile, csv, glob, io, os, sys

DATA_DIR = "data/daily/fno_bhavcopy"
KEEP_SYMBOLS = {"NIFTY", "BANKNIFTY"}

conn = sqlite3.connect("market.db")
c = conn.cursor()

def normalize_date(s):
    """Handle both '30-Jun-2022' and '2026-09-10'-style dates -> ISO."""
    s = s.strip()
    if '-' in s and len(s.split('-')[0]) == 4:
        return s  # already ISO
    months = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,
              'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
    d, mon, y = s.split('-')
    return f"{y}-{months[mon.upper()]:02d}-{int(d):02d}"

def parse_old_format(reader, errors):
    rows = []
    for row in reader:
        if row.get('SYMBOL') not in KEEP_SYMBOLS:
            continue
        try:
            instr = row['INSTRUMENT']
            expiry = normalize_date(row['EXPIRY_DT'])
            ts = normalize_date(row['TIMESTAMP'])
            common = (row['SYMBOL'], ts, expiry,
                      float(row['OPEN']), float(row['HIGH']), float(row['LOW']), float(row['CLOSE']),
                      float(row['SETTLE_PR']), int(float(row['CONTRACTS'])), float(row['VAL_INLAKH']),
                      int(float(row['OPEN_INT'])), int(float(row['CHG_IN_OI'])))
            if instr == 'FUTIDX':
                rows.append(('FUT', common))
            elif instr == 'OPTIDX':
                strike = float(row['STRIKE_PR'])
                opt_type = row['OPTION_TYP']
                data = common[:3] + (strike, opt_type) + common[3:]
                if len(data) != 14:
                    errors.append(f"bad tuple len {len(data)}: {row}")
                    continue
                rows.append(('OPT', data))
        except Exception as e:
            errors.append(f"row error: {e} | row={row}")
    return rows

def parse_new_format(reader, errors):
    """New NSE UDiFF bhavcopy (2024-07 onward): TradDt,TckrSymb,FinInstrmTp,..."""
    rows = []
    for row in reader:
        if row.get('TckrSymb') not in KEEP_SYMBOLS:
            continue
        try:
            instr = row['FinInstrmTp']
            expiry = row['XpryDt']  # already ISO
            ts = row['TradDt']      # already ISO
            common = (row['TckrSymb'], ts, expiry,
                      float(row['OpnPric']), float(row['HghPric']), float(row['LwPric']), float(row['ClsPric']),
                      float(row['SttlmPric']), int(float(row['TtlNbOfTxsExctd'])), float(row['TtlTrfVal']),
                      int(float(row['OpnIntrst'])), int(float(row['ChngInOpnIntrst'])))
            if instr == 'IDF':
                rows.append(('FUT', common))
            elif instr == 'IDO':
                strike = float(row['StrkPric'])
                opt_type = row['OptnTp']
                data = common[:3] + (strike, opt_type) + common[3:]
                if len(data) != 14:
                    errors.append(f"bad tuple len {len(data)}: {row}")
                    continue
                rows.append(('OPT', data))
        except Exception as e:
            errors.append(f"row error (new fmt): {e} | row={row}")
    return rows


def try_load_file(fpath, errors):
    z = zipfile.ZipFile(fpath)
    name = z.namelist()[0]
    raw = z.read(name).decode('utf-8', errors='replace')
    reader = csv.DictReader(io.StringIO(raw))
    fieldnames = reader.fieldnames or []
    if 'INSTRUMENT' in fieldnames:
        return parse_old_format(reader, errors)
    elif 'TckrSymb' in fieldnames:
        return parse_new_format(reader, errors)
    else:
        return None  # genuinely unknown format

files = sorted(glob.glob(f"{DATA_DIR}/*/*/*.zip"))
print(f"Found {len(files)} bhavcopy files")

n_fut, n_opt, n_skipped, unhandled = 0, 0, 0, []
row_errors = []

for i, fpath in enumerate(files):
    try:
        result = try_load_file(fpath, row_errors)
    except Exception as e:
        unhandled.append((fpath, str(e)))
        continue
    if result is None:
        unhandled.append((fpath, "new UDiFF format, not yet parsed"))
        continue
    for kind, data in result:
        if kind == 'FUT':
            c.execute("INSERT OR REPLACE INTO daily_futures VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", data)
            n_fut += 1
        else:
            c.execute("INSERT OR REPLACE INTO daily_options VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       data + (None, None, None, None, None))  # iv, delta, gamma, theta, vega = NULL for now
            n_opt += 1
    if (i+1) % 300 == 0:
        conn.commit()
        print(f"  ...{i+1}/{len(files)} files processed")

conn.commit()
print(f"\nFutures rows: {n_fut}")
print(f"Options rows: {n_opt}")
print(f"Unhandled files: {len(unhandled)}")
if unhandled:
    print("Sample unhandled:", unhandled[:3])
print(f"Row errors: {len(row_errors)}")
if row_errors:
    print("Sample row errors:", row_errors[:5])
conn.close()
