"""Stdlib-only supervisor for the local paper server, with bounded crash recovery."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import time


INTERRUPTED = {0, 130, -1073741510, 3221225786}


def supervise(command, log, run=subprocess.run, sleep=time.sleep, clock=time.monotonic):
    crashes = []
    while True:
        with log.open("ab", buffering=0) as stream:
            stream.write(f"\n[{datetime.now().isoformat()}] START\n".encode())
            try:
                result = run(command, stdout=stream, stderr=subprocess.STDOUT)
            except KeyboardInterrupt:
                return 0
            stream.write(f"\n[{datetime.now().isoformat()}] EXIT {result.returncode}\n".encode())
        if result.returncode in INTERRUPTED:
            return result.returncode
        now = clock()
        crashes = [t for t in crashes if now-t < 600] + [now]
        if len(crashes) >= 3:
            print(f"Stopped after 3 crashes in 10 minutes. Inspect {log}", flush=True)
            return result.returncode
        print(f"Paper server exited {result.returncode}; retry in {5*len(crashes)}s. Log: {log}", flush=True)
        try:
            sleep(5*len(crashes))
        except KeyboardInterrupt:
            return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--data-dir", type=Path, default=Path("data/v4-intraday-preview"))
    parser.add_argument("--model-python", type=Path, default=Path(".venv/Scripts/python.exe"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    os.environ["PYTHONPATH"] = str(root/"src") + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONUTF8"] = "1"
    logs = args.data_dir.resolve()/"logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs/f"server-{datetime.now():%Y%m%d-%H%M%S}.log"
    command = [sys.executable, "-X", "faulthandler", "-u", str(root/"scripts/launch_investor.py"),
               "--port", str(args.port), "--data-dir", str(args.data_dir.resolve()),
               "--model-python", str(args.model_python.resolve())]
    print(f"Paper server: http://127.0.0.1:{args.port}; log: {log}", flush=True)
    return supervise(command, log)


if __name__ == "__main__":
    raise SystemExit(main())
