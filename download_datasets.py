from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "datasets" / "raw"

KAGGLE_DATASETS = {
    "coughvid": "andrewmvd/covid19-cough-audio-classification",
    "icbhi": "vbookshelf/respiratory-sound-database",
}
COSWARA_REPO = "https://github.com/iiscleap/Coswara-Data.git"


def run(command: list[str]):
    print("$", " ".join(command))
    subprocess.run(command, check=True)


def download_coswara():
    target = RAW_DIR / "coswara"
    if target.exists():
        if (target / ".git" / "HEAD").exists():
            print(f"Coswara already exists at {target}")
            return
        print(f"Removing incomplete Coswara download at {target}")
        shutil.rmtree(target)
    if not shutil.which("git"):
        raise SystemExit("Git is required for Coswara. Install Git and rerun this command.")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--depth", "1", COSWARA_REPO, str(target)])
    print("Coswara repository downloaded. Run its extract_data.py to unpack split archives.")


def download_kaggle(name: str):
    target = RAW_DIR / name
    target.mkdir(parents=True, exist_ok=True)
    dataset_id = KAGGLE_DATASETS[name]
    command = [sys.executable, "-m", "kaggle", "datasets", "download", "-d", dataset_id, "-p", str(target), "--unzip"]
    try:
        run(command)
    except FileNotFoundError as error:
        raise SystemExit("Install the Kaggle CLI first: python -m pip install kaggle") from error
    except subprocess.CalledProcessError as error:
        raise SystemExit(
            f"Kaggle could not download {dataset_id}. Configure Kaggle credentials at "
            "%USERPROFILE%\\.kaggle\\kaggle.json and accept the dataset terms."
        ) from error


def main():
    parser = argparse.ArgumentParser(description="Download ECHO research datasets into datasets/raw.")
    parser.add_argument("dataset", nargs="?", choices=["coswara", "coughvid", "icbhi", "all"])
    args = parser.parse_args()
    if not args.dataset:
        parser.print_help()
        return
    names = ["coswara", "coughvid", "icbhi"] if args.dataset == "all" else [args.dataset]
    for name in names:
        if name == "coswara":
            download_coswara()
        else:
            download_kaggle(name)


if __name__ == "__main__":
    main()
