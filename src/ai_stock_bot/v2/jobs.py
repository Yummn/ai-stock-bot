from __future__ import annotations

import datetime as dt
import multiprocessing as mp
import os
import subprocess
import threading
import time
import traceback
from pathlib import Path

from .schema import AppConfig, JobRequest
from .store import Store
from .processes import terminate_worker_tree


def external_runtime(request, research_python=None, model_python=None):
    """Keep the web/ordinary simulation runtime separate from optional neural dependencies."""
    if request.kind == "intraday_test" and research_python:
        return research_python
    if not model_python:
        return None
    if request.kind == "portfolio_research":
        return model_python
    if not request.kind.startswith(("portfolio_", "intraday_")):
        return model_python
    if request.kind == "portfolio_test" and request.compare_models:
        return model_python
    method = request.research_method or (request.investment.method if request.investment else "")
    if method == "ml" and request.investment and request.investment.model_recipe != "legacy":
        from ..v4.model_recipes import model_specs
        if model_specs()[request.investment.model_recipe].model in {"cnn", "xgb"}:
            return model_python
    if request.kind in {"portfolio_test", "portfolio_update", "portfolio_frequency", "portfolio_observe"} and method.startswith("cnn"):
        return model_python
    return None


def execute_job(root, job_id):
    import faulthandler

    log_dir = Path(root) / "logs"
    log_dir.mkdir(exist_ok=True)
    native_log = (log_dir / f"{job_id}.native.log").open("a", encoding="utf-8")
    faulthandler.enable(file=native_log)

    store = Store(Path(root))
    request = JobRequest.model_validate(store.job(job_id, True)["request"])
    last_update, last_event, last_percent = 0., "", None

    def progress(percent, message):
        nonlocal last_update, last_event, last_percent
        now = time.monotonic()
        stage_changed = percent is not None and (last_percent is None or abs(percent - last_percent) >= 3)
        if now - last_update > .4 or stage_changed:
            fields = {"message": message}
            if percent is not None:
                fields["progress"] = percent
            store.update_job(job_id, **fields)
            last_update = now
            if percent is not None:
                last_percent = percent
        if message != last_event and ("训练 " in message or "获取 " in message or "验证 " in message and " / " not in message):
            store.event(job_id, message)
            last_event = message

    stop = threading.Event()
    def heartbeat():
        while not stop.wait(10):
            store.update_job(job_id)
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        store.update_job(job_id, status="running", message="计算进程已启动", progress=1, error=None)
        store.event(job_id, "计算进程已启动")
        if request.kind.startswith("portfolio_") or request.kind.startswith("intraday_"):
            from ..v4.service import comparison, frequency_comparison, update_portfolio, review_portfolio
            from ..v4.data import refresh_intraday
            if request.kind == "portfolio_test":
                result = comparison(store, job_id, request, progress)
            elif request.kind == "portfolio_research":
                from ..v4.lab import run_study
                result = run_study(store, job_id, request, progress)
            elif request.kind == "portfolio_frequency":
                result = frequency_comparison(store, request, progress)
            elif request.kind == "portfolio_update":
                result = update_portfolio(store, request.investment, progress)
            elif request.kind == "portfolio_observe":
                from ..v4.observation import update_observation
                result = update_observation(store, request.observation_id, progress)
            elif request.kind == "portfolio_review":
                result = review_portfolio(store, request.investment, progress)
            elif request.kind == "intraday_test":
                from ..v4.intraday import research
                plan = request.investment
                result = research(store, plan, plan.research_symbol, plan.research_annual_rate, progress)
            else:
                result = refresh_intraday(store, request.investment.symbols, progress,
                                          request.investment.research_interval)
        elif request.kind == "backtest":
            from .backtest import run_backtest
            result = run_backtest(store, request, progress)
        elif request.kind == "evaluate":
            from .evaluation import run_evaluation
            result = run_evaluation(store, job_id, request, progress)
        elif request.kind == "learn":
            from .learning import run_learning
            if request.config.engine != "ml":
                raise ValueError("自动学习请选择机器学习运行模式")
            result = run_learning(store, job_id, request, progress)
        elif request.kind == "paper":
            from .paper import run_paper
            result = run_paper(store, request.config, progress)
        elif request.kind == "research":
            from .research import run_research
            result = run_research(store, request.config, progress)
        else:
            from .market import Market, today
            market = Market(store)
            symbols = list(dict.fromkeys(request.config.symbols + request.config.training_symbols))
            end = today() - dt.timedelta(days=1)
            start = end - dt.timedelta(days=365 * 11)
            failures = []
            for index, symbol in enumerate(symbols):
                try:
                    market.ensure(symbol, start, end, lambda m: progress(5 + 90 * index / len(symbols), m), refresh=True)
                except Exception as exc:
                    failures.append({"symbol": symbol, "error": str(exc)[:500]})
            result = {"coverage": market.coverage(symbols), "errors": failures}
        store.update_job(job_id, status="done", progress=100, message="任务完成", result=result)
        store.event(job_id, "任务完成，结果已保存")
    except BaseException as exc:
        store.update_job(job_id, status="failed", message="任务失败，可查看日志或重试", error=str(exc)[:2000])
        store.event(job_id, traceback.format_exc())
    finally:
        stop.set()
        faulthandler.disable()
        native_log.close()


class JobManager:
    def __init__(self, store: Store):
        self.store = store
        self.active = None
        self.lock = threading.Lock()
        self.closed = threading.Event()
        with store.connect() as c:
            c.execute("UPDATE jobs SET status='interrupted',message='服务曾中断，可继续任务' WHERE status IN ('running','queued')")
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        scheduled = 0.
        while not self.closed.wait(.5):
            with self.lock:
                if time.monotonic() - scheduled >= 60:
                    from ..v4.schedule import enqueue_daily
                    from ..v4.observation import enqueue_observation
                    try:
                        enqueue_observation(self.store)
                        enqueue_daily(self.store)
                    except Exception as exc:
                        self.store.set_meta('portfolio_scheduler_error', str(exc)[:500])
                    scheduled = time.monotonic()
                if self.active:
                    job_id, process = self.active
                    if process.is_alive():
                        continue
                    process.join()
                    job = self.store.job(job_id)
                    if job["status"] == "running":
                        detail = f"计算进程退出 {process.exitcode} / 0x{(process.exitcode or 0) & 0xffffffff:08X}"
                        self.store.update_job(job_id, status="failed", error=detail, message="计算已中断；网页与已保存结果仍在，可继续任务")
                        self.store.event(job_id, detail)
                    process.close()
                    self.active = None
                with self.store.connect() as c:
                    queued = c.execute("SELECT id FROM jobs WHERE status='queued' "
                                       "ORDER BY CASE WHEN kind='portfolio_observe' THEN 0 ELSE 1 END,created LIMIT 1").fetchone()
                if queued:
                    job_id = queued[0]
                    self.store.update_job(job_id, status="running", message="启动独立计算进程")
                    process = None
                    try:
                        request = JobRequest.model_validate(self.store.job(job_id, True)["request"])
                        selected_python = external_runtime(request, os.environ.get("STOCKLAB_RESEARCH_PYTHON"),
                                                           os.environ.get("STOCKLAB_MODEL_PYTHON"))
                        if selected_python:
                            process = ResearchProcess(selected_python, str(self.store.root), job_id)
                        else:
                            process = mp.get_context("spawn").Process(target=execute_job, args=(str(self.store.root), job_id))
                        process.start()
                    except Exception as exc:
                        self.store.update_job(job_id, status="failed", error=str(exc)[:2000], message="无法启动计算环境；网页仍可使用")
                        self.store.event(job_id, traceback.format_exc())
                        if process is not None:
                            try:
                                process.close()
                            except Exception as cleanup_error:
                                self.store.event(job_id, f"计算环境清理失败：{cleanup_error}")
                        continue
                    self.active = job_id, process

    def stop(self, job_id):
        with self.lock:
            job = self.store.job(job_id)
            if job["status"] not in {"queued", "running"}:
                return job
            if self.active and self.active[0] == job_id:
                process = self.active[1]
                if process.is_alive():
                    terminate_worker_tree(process.pid)
                    process.join(3)
                    if process.is_alive():
                        process.kill()
                        process.join(3)
                process.close()
                self.active = None
            self.store.update_job(job_id, status="stopped", message="已停止，完成的候选已保存")
            return self.store.job(job_id)

    def resume(self, job_id):
        with self.lock:
            job = self.store.job(job_id)
            if job["status"] not in {"stopped", "failed", "interrupted"}:
                raise ValueError("仅停止、失败或中断的任务可以继续")
            self.store.update_job(job_id, status="queued", message="等待继续；已完成候选无需重算", error=None)
        return self.store.job(job_id)

    def close(self):
        self.closed.set()
        with self.lock:
            if self.active:
                job_id, process = self.active
                if process.is_alive():
                    terminate_worker_tree(process.pid)
                    process.join(3)
                self.store.update_job(job_id, status="interrupted", message="服务关闭，可继续")


class ResearchProcess:
    """An isolated worker; it cannot bring down the web server on native failure."""
    def __init__(self, executable, root, job_id):
        self.executable, self.root, self.job_id = executable, root, job_id
        self.process = None
        self.log = None

    def start(self):
        logs = Path(self.root) / "logs"
        logs.mkdir(exist_ok=True)
        self.log = (logs / f"{self.job_id}.runtime.log").open("a", encoding="utf-8")
        env = dict(os.environ)
        source = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
        try:
            self.process = subprocess.Popen(
                [self.executable, "-X", "faulthandler", "-m", "ai_stock_bot.v2.jobs", self.root, self.job_id],
                env=env, stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except Exception:
            self.log.close()
            raise

    @property
    def pid(self):
        return self.process.pid

    @property
    def exitcode(self):
        return self.process.poll()

    def is_alive(self):
        return self.process.poll() is None

    def join(self, timeout=None):
        try:
            self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            pass

    def kill(self):
        self.process.kill()

    def close(self):
        if self.log:
            self.log.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        raise SystemExit("Expected data directory and research job id")
    selected_store = Store(Path(sys.argv[1]))
    JobRequest.model_validate(selected_store.job(sys.argv[2], True)["request"])
    execute_job(sys.argv[1], sys.argv[2])
