import importlib.util
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("supervise_paper", Path(__file__).resolve().parents[1]/"scripts/supervise_paper.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_recovers_once_and_records_native_exit(tmp_path):
    codes = iter([3221225477, 0])
    waits = []
    log = tmp_path/"server.log"
    assert module.supervise([], log, run=lambda *a, **k: SimpleNamespace(returncode=next(codes)),
                            sleep=waits.append, clock=lambda: 10) == 0
    assert waits == [5]
    assert "3221225477" in log.read_text() and "EXIT 0" in log.read_text()


def test_crash_loop_is_bounded(tmp_path):
    waits = []
    assert module.supervise([], tmp_path/"server.log", run=lambda *a, **k: SimpleNamespace(returncode=1),
                            sleep=waits.append, clock=lambda: 10) == 1
    assert waits == [5, 10]


def test_explicit_interruption_does_not_restart(tmp_path):
    waits = []
    module.supervise([], tmp_path/"server.log", run=lambda *a, **k: SimpleNamespace(returncode=3221225786),
                     sleep=waits.append)
    assert waits == []
