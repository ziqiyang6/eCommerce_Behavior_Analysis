from __future__ import annotations

import shutil
from pathlib import Path
import kagglehub

DATASET = "mkechinov/ecommerce-behavior-data-from-multi-category-store"
TARGET_DIR = Path("data/raw/")  # data path
PATTERNS = ("*.csv", "*.csv.gz")                  # 

def sync_dataset_to_project_dir(dataset: str = DATASET, target_dir: Path = TARGET_DIR) -> Path:
    """
    Idempotent: download (cached by kagglehub) and sync all available CSV/CSV.GZ files
    into project data/raw directory. Subsequent runs do nothing if files already exist.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    cache_path = Path(kagglehub.dataset_download(dataset))
    print("kagglehub cache:", cache_path)

    # 自动发现 cache 中“当前可获得”的文件（你现在只有两个月就只会发现两个月）
    files = []
    for pat in PATTERNS:
        files.extend(sorted(cache_path.glob(pat)))
    files = [p for p in files if p.is_file()]

    if not files:
        raise RuntimeError(f"No CSV files found in kagglehub cache path: {cache_path}")

    copied = 0
    skipped = 0
    for src in files:
        dst = target_dir / src.name
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            skipped += 1
            continue
        shutil.copy2(src, dst)
        copied += 1

    print(f"✅ Synced to {target_dir.resolve()}: copied={copied}, skipped={skipped}, total={len(files)}")
    return target_dir

if __name__ == "__main__":
    out = sync_dataset_to_project_dir()
    print("Use this path for Spark:", str(out / "*.csv*"))

