"""Isolated personal-investment edition; previous data is copied, never moved."""
import argparse
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

from launch_workbench import migrate


def main():
    parser = argparse.ArgumentParser(description="StockLab personal investment")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--data-dir", type=Path, help="Use an explicitly isolated data directory")
    parser.add_argument("--research-python", type=Path, help="Separate interpreter for read-only intraday research only")
    parser.add_argument("--model-python", type=Path, help="Keep neural/legacy workers in their existing dependency environment")
    args = parser.parse_args()
    if args.research_python:
        if not args.research_python.is_file():
            parser.error("Research Python executable not found")
        os.environ["STOCKLAB_RESEARCH_PYTHON"] = str(args.research_python.resolve())
    if args.model_python:
        if not args.model_python.is_file():
            parser.error("Model Python executable not found")
        os.environ["STOCKLAB_MODEL_PYTHON"] = str(args.model_python.resolve())
    if not 1 <= args.port <= 65535:
        parser.error("Invalid port")
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(name, "2")
    url = f"http://127.0.0.1:{args.port}"
    try:
        with urllib.request.urlopen(url + "/api/v2/health", timeout=2) as response:
            health = json.load(response)
        if health.get("version") == "4.8.0":
            print(f"Already running: {url}")
            return
        raise SystemExit(f"Port {args.port} is occupied. Choose another port.")
    except urllib.error.HTTPError:
        raise SystemExit(f"Port {args.port} is occupied. Choose another port.")
    except urllib.error.URLError:
        pass
    target = args.data_dir.resolve() if args.data_dir else root / "data" / "v4"
    for candidate in (() if args.data_dir else ("v3-hybrid-preview", "v3", "v2")):
        previous = root / "data" / candidate
        if (previous / "stocklab.sqlite3").exists():
            migrate(previous, target)
            break
    from ai_stock_bot.v2.api import create_app
    import uvicorn
    print(f"Personal investment: {url}; data: {target}")
    uvicorn.run(create_app(target, static_root=root / "src" / "ai_stock_bot" / "v4" / "static"), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
