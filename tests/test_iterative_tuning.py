import importlib.util
from pathlib import Path

import pytest

from ai_stock_bot.v2.schema import AppConfig, MODEL_NAMES, PROFILES

spec = importlib.util.spec_from_file_location("iterate_stocklab", Path(__file__).parents[1] / "scripts/iterate_stocklab.py")
research = importlib.util.module_from_spec(spec)
spec.loader.exec_module(research)


@pytest.mark.parametrize("model", list(MODEL_NAMES))
@pytest.mark.parametrize("profile", list(PROFILES))
def test_all_twenty_pairs_have_valid_manual_and_capacity_candidates(model, profile):
    config = AppConfig()
    before = config.model_dump()
    candidates = research.manual_presets(config.models[model][profile], model, profile)
    assert {p.horizon for _, p in candidates} == {1, 5}
    assert {p.target for _, p in candidates} == {"binary", "trend5"}
    candidates += research.inactive_repairs(config.models[model][profile], model, profile)
    for _, p in candidates:
        for _, candidate in research.model_neighbors(p, model):
            changed = config.model_copy(deep=True)
            changed.models[model][profile] = candidate
            AppConfig.model_validate(changed.model_dump())
            assert changed.risk == config.risk
            assert changed.local == config.local and changed.ai == config.ai
    assert config.model_dump() == before


def row(value, benchmark=.02, dd=-.02, exposure=.4):
    return dict(return_pct=value, benchmark_return=benchmark, excess_return=value-benchmark, max_drawdown=dd, mean_exposure=exposure)


def test_custom_is_evaluated_independently_and_inactivity_is_not_bull_success():
    assert research.objective([row(.1)], "custom") > research.objective([row(0)], "custom")
    inactive = row(0, benchmark=.2, dd=0, exposure=0)
    active = row(.05, benchmark=.2, dd=-.03, exposure=.3)
    assert research.objective([active], "aggressive") > research.objective([inactive], "aggressive")
    assert research.objective([row(.01, dd=-.2)], "conservative") < research.objective([row(0, dd=0)], "conservative")


def test_probability_feedback_has_valid_thresholds_and_does_not_modify_model():
    config = AppConfig()
    p = config.models["cnn"]["custom"]
    rows = [dict(trade_count=0, probability_quantiles=[.01, .02, .03, .04, .05, .1])]
    candidates = research.threshold_neighbors(p, rows)
    assert len(candidates) == 5
    for _, candidate in candidates:
        assert 0 <= candidate.sell_threshold < candidate.buy_threshold <= 1
        assert candidate.params == p.params
    assert candidates[-1][1].buy_threshold < p.buy_threshold


def test_checkpoint_retries_transient_windows_reader_lock(tmp_path, monkeypatch):
    replace, attempts = Path.replace, []
    def locked(path, target):
        attempts.append(path)
        if len(attempts) < 3:
            raise PermissionError("temporary reader lock")
        return replace(path, target)
    monkeypatch.setattr(Path, "replace", locked)
    monkeypatch.setattr(research.time, "sleep", lambda _:None)
    target = tmp_path / "checkpoint.json"
    research.write_json(target, {"complete":True})
    assert len(attempts) == 3 and 'true' in target.read_text(encoding="utf-8")


def test_release_drawdown_tradeoff_is_disclosed_and_only_for_defensive_profiles():
    old, new = dict(score=0), dict(score=.01)
    before = dict(score=.2, per_year=[dict(return_pct=.1, max_drawdown=-.03)])
    after = dict(score=.05, per_year=[dict(return_pct=.02, max_drawdown=-.01)])
    accepted, reason = research.release_acceptance("conservative", old, new, before, after)
    assert accepted and "tradeoff" in reason
    assert not research.release_acceptance("aggressive", old, new, before, after)[0]
    after["per_year"][0]["return_pct"] = -.001
    assert not research.release_acceptance("balanced", old, new, before, after)[0]
