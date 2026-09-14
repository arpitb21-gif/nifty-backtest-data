from huggingface_hub import HfApi, hf_hub_download
import shutil, os

REPO_ID = "thetrademarkk/india-index-options-1m"
DEST = "data/minute"
KEEP = ("NIFTY", "BANKNIFTY")  # excludes SENSEX

api = HfApi()
files = api.list_repo_files(REPO_ID, repo_type="dataset")

wanted = [
    f for f in files
    if f.endswith(".parquet")
    and any(f"/{sym}" in f or f.startswith(f"index/{sym}") for sym in KEEP)
]

print(f"Found {len(wanted)} files to download")

for f in wanted:
    local_path = hf_hub_download(REPO_ID, f, repo_type="dataset")
    dest_path = os.path.join(DEST, f)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    shutil.copy(local_path, dest_path)
    print(f"Saved {dest_path}")

print("Done.")
