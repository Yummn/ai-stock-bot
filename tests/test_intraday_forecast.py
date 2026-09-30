import copy
import datetime as dt

import pytest

from ai_stock_bot.v4.intraday_forecast import IntradayForecast, features, training_rows
from ai_stock_bot.v4.intraday_quality import continuous, quality
from ai_stock_bot.v4.intraday import _replay, walk_forward
from ai_stock_bot.v4.intraday_engine import IntradayEngine, signal
from ai_stock_bot.v4.schema import Plan
from test_intraday_engine import bars


def test_intraday_task_does_not_require_legacy_model_config(tmp_path):
    from ai_stock_bot.v2.schema import JobRequest
    from ai_stock_bot.v2.store import Store
    job = Store(tmp_path).create_job(JobRequest(kind="intraday_test", investment=Plan(research_interval="30m")).model_dump())
    assert job["status"] == "queued"
    assert job["summary"]["engine"] == "portfolio"


def test_missing_research_runtime_does_not_stop_queue(tmp_path, monkeypatch):
    import time
    from ai_stock_bot.v2.jobs import JobManager
    from ai_stock_bot.v2.schema import JobRequest
    from ai_stock_bot.v2.store import Store
    monkeypatch.setenv("STOCKLAB_RESEARCH_PYTHON", str(tmp_path / "missing-python.exe"))
    store = Store(tmp_path)
    manager = JobManager(store)
    try:
        job = store.create_job(JobRequest(kind="intraday_test", investment=Plan(research_interval="30m")).model_dump())
        for _ in range(50):
            current = store.job(job["id"])
            if current["status"] == "failed":
                break
            time.sleep(.1)
        assert current["status"] == "failed"
        assert manager.thread.is_alive()
    finally:
        manager.close()


def test_missing_candles_not_compressed_into_training_samples():
    plan = Plan(research_interval="30m", intraday_strategy="ml_edge")
    rows = bars(25, half=True)
    assert continuous(rows, "30m")
    missing = rows.pop(-4)
    assert not continuous(rows, "30m")
    assert features(rows, "30m") is None
    assert quality(rows, "30m")["missing_bars"] == 1
    assert all(not (r["feature_time"] < missing["stamp"] < r["label_end"])
               for r in training_rows(rows, plan))
    assert signal(rows, plan)["weight"] == 0


def test_training_labels_use_delayed_entry_and_past_maturity():
    plan = Plan(research_interval="30m", intraday_strategy="ml_edge", intraday_min_samples=60)
    rows = bars(30, half=True)
    examples = training_rows(rows, plan)
    assert examples
    for item in examples:
        opening = dt.datetime.fromisoformat(item["entry_time"]) - dt.timedelta(minutes=30)
        assert opening >= dt.datetime.fromisoformat(item["feature_time"]) + dt.timedelta(minutes=10)
        assert item["entry_time"] < item["label_end"]
    first = IntradayForecast(plan).at(rows)
    assert first["status"] in {"model", "no_edge"}
    assert first["last_label_end"][:10] < first["fit_day"]
    assert first["fit_label_end"] < first["calibration_start"]
    if first["status"] == "no_edge":
        assert first["gross_prediction"] is None
        assert first["calibration_mae"] >= first["zero_forecast_mae"]
    changed = copy.deepcopy(rows)
    for row in changed[-4:]:
        for key in ("open", "high", "low", "close"):
            row[key] *= 1.3
    # Predictions on an earlier prefix cannot depend on later candle revisions.
    assert IntradayForecast(plan).at(changed[:-4]) == IntradayForecast(plan).at(rows[:-4])


def test_not_enough_labels_stays_cash_not_hidden_rule_fallback():
    plan = Plan(research_interval="60m", intraday_strategy="ml_edge", intraday_min_samples=10000)
    result = _replay(bars(12), plan, 3, .08)
    assert not result["trades"]
    assert result["return_pct"] == 0
    assert any(c["status"] == "insufficient" for c in result["model_checks"])


def test_risk_exit_is_not_reissued_each_bar():
    plan = Plan(research_interval="30m", intraday_strategy="legacy")
    engine = IntradayEngine(plan)
    rows = bars(10, half=True)
    index = 16
    engine.account.new_day(rows[index]["stamp"][:10])
    trade, _ = engine.account.trade(plan.research_symbol, "buy", rows[index]["open"],
                                    rows[index]["stamp"][:10], "fixture", "intraday", quantity=1000)
    assert trade
    engine.state["risk_peak"] = 150000
    for i in range(index, index + 3):
        engine.step(rows[i], rows[:i], issued_at=(dt.datetime.fromisoformat(rows[i]["stamp"]) + dt.timedelta(minutes=10)).isoformat())
    sells = [t for t in engine.account.trades if t["side"] == "sell"]
    assert len(sells) == 1
    assert sells[0]["signal_date"] == rows[index]["stamp"]
    assert sells[0]["stamp"] == rows[index+2]["stamp"]


def test_forward_slices_share_dates_and_do_not_promote_winner():
    plan = Plan(research_interval="60m", intraday_strategy="ml_edge")
    result = walk_forward(bars(40), plan)
    assert len(result["strategies"]) == 4
    assert result["status"] == "not_qualified" and result["applied"] is False
    starts = {s["periods"][0]["start"] for s in result["strategies"]}
    assert len(starts) == 1
    assert all(s["periods"][0]["hold"]["multiple"] == 1 for s in result["strategies"])
