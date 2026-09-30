"""Release native neural runtimes between annual evaluations, without a time limit."""
from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path
from queue import Empty, Full
import tempfile
import threading

from .backtest import run_backtest as direct_backtest
from .schema import JobRequest
from .store import Store


def _annual_worker(root, request, histories, output, messages):
    import faulthandler
    from .evaluation import write_gzip

    parent = mp.parent_process()
    stopped = threading.Event()

    def watch_parent():
        while not stopped.wait(1):
            if parent is not None and not parent.is_alive():
                os._exit(1)

    threading.Thread(target=watch_parent, daemon=True).start()

    def progress(percent, message):
        try:
            messages.put_nowait((percent, message))
        except Full:
            pass

    log = Path(root) / "logs" / "annual-native.log"
    log.parent.mkdir(exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        faulthandler.enable(file=handle)
        try:
            result = direct_backtest(Store(Path(root)), JobRequest.model_validate(request), progress, histories)
            write_gzip(Path(output), {"result": result})
        except Exception as exc:
            write_gzip(Path(output), {"error": f"{type(exc).__name__}: {exc}"})
        finally:
            stopped.set()
            messages.cancel_join_thread()
            faulthandler.disable()


def run_backtest(store, request, progress=lambda percent, message: None, histories=None):
    if request.config.engine != "ml" or request.config.model not in {"mlp", "cnn"}:
        return direct_backtest(store, request, progress, histories)
    from .evaluation import read_gzip

    ctx = mp.get_context("spawn")
    workers = store.root / "workers"
    workers.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=workers) as directory:
        output = Path(directory) / "result.json.gz"
        for attempt in range(2):
            messages = ctx.Queue(maxsize=16)
            process = ctx.Process(target=_annual_worker, args=(str(store.root), request.model_dump(), histories, str(output), messages), daemon=True)
            process.start()
            try:
                while process.is_alive():
                    try:
                        percent, message = messages.get(timeout=.2)
                        progress(percent, message)
                    except Empty:
                        pass
                process.join()
                code = process.exitcode
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join()
                process.close()
                messages.close()
                messages.join_thread()
            if code == 0 and output.exists():
                payload = read_gzip(output)
                if "error" in payload:
                    raise RuntimeError(payload["error"])
                return payload["result"]
            if attempt == 0:
                progress(0, f"年度进程异常退出 {code}；释放计算资源后重试一次")
        raise RuntimeError(f"年度进程连续异常退出 {code}；该年无有效结果，不能参与最优排名。详见 annual-native.log")
