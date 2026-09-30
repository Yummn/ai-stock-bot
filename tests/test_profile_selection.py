from collections import deque
from types import SimpleNamespace

import numpy as np
import pandas as pd

from ai_stock_bot.v2 import profile_selection
from ai_stock_bot.v2.evaluation import find_evidence, strategy_key
from ai_stock_bot.v2.profile_selection import RECENT_SESSIONS, RecentSelector, preview_profiles, selection_mode
from ai_stock_bot.v2.schema import AppConfig, JobRequest
from ai_stock_bot.v2.store import Store


def test_legacy_auto_profile_migrates_and_evidence_is_independent():
    config = AppConfig(engine="ml")
    old = config.model_dump()
    old.pop("profile_selection")
    old["auto_profile"] = True
    trend = AppConfig.model_validate(old)
    recent = trend.model_copy(deep=True)
    recent.profile_selection = "recent"
    assert selection_mode(trend) == "trend"
    assert selection_mode(recent) == "recent"
    assert strategy_key(trend) != strategy_key(recent)


def test_recent_selector_requires_completed_past_window(monkeypatch):
    config = AppConfig(engine="ml", profile_selection="recent", symbols=["510300.SH"])
    symbol = config.symbols[0]
    selector = RecentSelector(config, [symbol])
    monkeypatch.setattr(profile_selection, "decide", lambda *args, **kwargs: {"side": "buy", "reason": "测试"})
    dates = pd.bdate_range("2024-01-02", periods=RECENT_SESSIONS + 1)
    for i, day in enumerate(dates):
        if i < RECENT_SESSIONS:
            chosen, reason, returns = selector.choose(config, symbol, "sideways")
            assert chosen == "balanced"
            assert returns["aggressive"] is None
        price = 3.0 * (1 + .002 * i)
        row = SimpleNamespace(open=price, close=price)
        selector.observe(symbol, day, row, pd.Series([1.0]), "sideways",
                         {p: np.array([.1, .9]) for p in profile_selection.PROFILES})
    chosen, reason, returns = selector.choose(config, symbol, "sideways")
    assert chosen == "aggressive"
    assert returns["aggressive"] > 0


def test_recent_selector_downgrades_only_with_valid_net_results():
    config = AppConfig(engine="ml", profile_selection="recent", symbols=["510300.SH"])
    symbol = config.symbols[0]
    selector = RecentSelector(config, [symbol])
    aggressive = selector.trackers[symbol, "aggressive"]
    balanced = selector.trackers[symbol, "balanced"]
    aggressive.history = deque([(100_000, 1)] * RECENT_SESSIONS + [(98_000, 2)], maxlen=RECENT_SESSIONS + 1)
    aggressive.valid_history = deque([True] * RECENT_SESSIONS, maxlen=RECENT_SESSIONS)
    chosen, reason, _ = selector.choose(config, symbol, "bull")
    assert chosen == "aggressive" and "不足" in reason
    balanced.history = deque([(100_000, 1)] * RECENT_SESSIONS + [(101_000, 2)], maxlen=RECENT_SESSIONS + 1)
    balanced.valid_history = deque([True] * RECENT_SESSIONS, maxlen=RECENT_SESSIONS)
    assert selector.choose(config, symbol, "bear")[0] == "balanced"
    balanced.history[-1] = (99_000, 2)
    assert selector.choose(config, symbol, "bull")[0] == "conservative"


def test_each_selection_method_has_its_own_history(tmp_path):
    store = Store(tmp_path)
    ids = {}
    for mode, gain in (("trend", .03), ("recent", -.02)):
        config = AppConfig(engine="ml", profile_selection=mode)
        job = store.create_job(JobRequest(kind="evaluate", years=[2022], config=config).model_dump())
        store.update_job(job["id"], status="done", result={"per_year": [{
            "year": 2022, "return_pct": gain, "excess_return": gain + .1,
            "benchmark_return": -.1, "max_drawdown": -.04,
        }]})
        ids[mode] = job["id"]
    for mode, gain in (("trend", .03), ("recent", -.02)):
        evidence = find_evidence(store, AppConfig(engine="ml", profile_selection=mode))
        assert evidence["job_id"] == ids[mode]
        assert evidence["scorecard"]["mean_return"] == gain


def test_paper_recent_mode_trains_only_before_signal_date(tmp_path, monkeypatch):
    from ai_stock_bot.v2 import paper

    config = AppConfig(engine="ml", profile_selection="recent", symbols=["510300.SH"])
    dates = pd.bdate_range(end="2025-12-30", periods=130)
    price = 3 * (1 + np.arange(len(dates)) * .001)
    frame = pd.DataFrame({"open": price, "high": price * 1.01, "low": price * .99,
                          "close": price, "adjusted": price, "volume": 1000}, index=dates)
    monkeypatch.setattr(paper, "today", lambda: pd.Timestamp("2025-12-31").date())
    monkeypatch.setattr(paper, "load_histories", lambda *args: ({config.symbols[0]: frame}, []))
    cutoffs = []

    def prediction(store, histories, cfg, preset, cutoff, symbol, profile, progress, workspace=None):
        cutoffs.append(cutoff)
        return np.tile([.1, .9], (len(frame), 1)), {"profile": profile}

    monkeypatch.setattr(paper, "probabilities", prediction)
    result = paper.run_paper(Store(tmp_path), config, lambda *args: None)
    signal_date = dates[-2]
    assert len(cutoffs) == 4
    assert max(cutoffs) == signal_date
    assert min(cutoffs) == dates[-62]
    assert result["decisions"][0]["profile_selection"] == "recent"
    assert result["decisions"][0]["profile"] == "aggressive"
    assert Store(tmp_path).meta("last_decisions_strategy_key") == strategy_key(config)


def test_preview_shows_trend_reason_and_never_reuses_other_plan(tmp_path, monkeypatch):
    from ai_stock_bot.v2.market import Market

    store = Store(tmp_path)
    symbol = "510300.SH"
    dates = pd.bdate_range(end="2025-12-30", periods=90)
    price = 3 * (1 + np.arange(len(dates)) * .003)
    frame = pd.DataFrame({"open": price, "high": price * 1.01, "low": price * .99,
                          "close": price, "adjusted": price, "volume": 1000}, index=dates)
    monkeypatch.setattr(Market, "read", lambda self, symbol, start=None, end=None: frame)
    trend = AppConfig(engine="ml", profile_selection="trend", symbols=[symbol])
    row = preview_profiles(store, trend)["rows"][0]
    assert row["profile"] == "aggressive"
    assert "近20日涨幅" in row["reason"]
    recent = AppConfig(engine="ml", profile_selection="recent", symbols=[symbol])
    store.set_meta("last_decisions", [{"symbol": symbol, "profile": "balanced", "date": "2025-12-29",
                                       "profile_selection": "recent", "profile_selection_reason": "测试理由"}])
    store.set_meta("last_decisions_strategy_key", strategy_key(trend))
    assert preview_profiles(store, recent)["rows"][0]["profile"] is None
    store.set_meta("last_decisions_strategy_key", strategy_key(recent))
    matched = preview_profiles(store, recent)["rows"][0]
    assert matched["profile"] == "balanced" and matched["reason"] == "测试理由"
