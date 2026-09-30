"""Launch the workbench and copy the previous store on first upgrade."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request


def migrate(previous: Path, target: Path):
    database = target / "stocklab.sqlite3"
    if database.exists() or not (previous / "stocklab.sqlite3").exists():
        return
    target.mkdir(parents=True, exist_ok=True)
    temporary = target / "stocklab.upgrade.sqlite3"
    with closing(sqlite3.connect(previous / "stocklab.sqlite3")) as source:
        with closing(sqlite3.connect(temporary)) as destination:
            source.backup(destination)
    # Publish the database only once its file-backed experiment results exist.
    for name in ("evaluations", "learning_checks"):
        if (previous / name).exists():
            shutil.copytree(previous / name, target / name, dirs_exist_ok=True)
    temporary.replace(database)
    print(f"Previous settings, account and experiments copied to {target}.")


def main():
    parser = argparse.ArgumentParser(description="StockLab 3.0")
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    # Match estimator parallelism before scientific libraries initialize.
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(name, "2")
    url = f"http://127.0.0.1:{args.port}"
    try:
        with urllib.request.urlopen(url + "/api/v2/health", timeout=2) as response:
            health = json.load(response)
        if health.get("version") == "3.0.0":
            print(f"StockLab is already running: {url}")
            return
        raise SystemExit(f"Port {args.port} is occupied. Choose another port.")
    except urllib.error.HTTPError:
        raise SystemExit(f"Port {args.port} is occupied. Choose another port.")
    except urllib.error.URLError:
        pass
    target = root / "data" / "v3"
    migrate(root / "data" / "v2", target)
    print(f"StockLab 3.0: {url}")
    from ai_stock_bot.v2.api import main as serve
    sys.argv = [sys.argv[0], "--port", str(args.port), "--data-dir", str(target)]
    serve()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
