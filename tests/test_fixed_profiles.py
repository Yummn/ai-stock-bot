import json
from pathlib import Path

import pytest

from ai_stock_bot.v2.store import Store, ConflictError
from ai_stock_bot.v4.schema import FIXED_PROFILE_PRESETS, Plan, fingerprint, comparison_fingerprint
from ai_stock_bot.v4.service import save_settings, settings
from ai_stock_bot.v4.preset_evidence import KEY, current_evidence


def fixed(key, **extra):
    preset = {k: v for k, v in FIXED_PROFILE_PRESETS[key].items() if k != "label"}
    return Plan.model_validate({**preset, "profile": key, "preset_version": 1, **extra})


@pytest.mark.parametrize("key", FIXED_PROFILE_PRESETS)
def test_complete_presets_save_and_reload(tmp_path, key):
    store = Store(tmp_path)
    plan = fixed(key, starting_cash=120000, auto_update=False)
    saved = save_settings(store, plan, 1)
    assert settings(store) == saved
    assert saved["plan"]["profile"] == key
    with pytest.raises(ConflictError):
        save_settings(store, plan, 1)


@pytest.mark.parametrize("key", ["conservative", "balanced"])
def test_switch_removes_aggressive_risk(key):
    aggressive = fixed("aggressive").model_dump()
    preset = {k: v for k, v in FIXED_PROFILE_PRESETS[key].items() if k != "label"}
    plan = Plan.model_validate({**aggressive, **preset, "profile": key})
    assert plan.profile == key
    assert plan.research_multiple == 1
    assert plan.target_volatility < .30
    assert plan.max_exposure*plan.research_multiple <= 1
    assert plan.model_recipe == "hgb20_regularized"


@pytest.mark.parametrize("field,value", [("research_multiple", 3), ("target_volatility", .2),
    ("rebalance", "weekly"), ("execution_policy", "legacy"), ("method", "rotation"),
    ("commission_rate", .001)])
def test_modified_preset_becomes_custom(field, value):
    assert fixed("aggressive", **{field: value}).profile == "custom"


def test_aggressive_matches_published_execution_and_preserves_old_hash():
    path = Path(__file__).resolve().parents[1]/"docs/reports/execution-release-20260930/verified-plan.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    old = Plan.model_validate(record["plan"])
    assert fingerprint(old) == record["plan_key"]
    assert comparison_fingerprint(old) == record["result"]["comparison_key"]
    new = fixed("aggressive")
    for key in FIXED_PROFILE_PRESETS["aggressive"]:
        if key != "label":
            assert getattr(new, key) == getattr(old, key), key
    assert fingerprint(new) == fingerprint(old)
    assert fingerprint(fixed("balanced")) != fingerprint(old)


@pytest.mark.parametrize("profile", ["conservative", "balanced"])
def test_balanced_matches_locked_verified_candidate(profile):
    root = Path(__file__).resolve().parents[1]/"docs/reports/core-profiles-20260930"
    verified = json.loads((root/"verified-plans.json").read_text(encoding="utf-8"))[profile]
    results = json.loads((root/"results.json").read_text(encoding="utf-8"))
    plan = fixed(profile)
    assert fingerprint(plan) == verified["plan_key"]
    assert results["gates"][profile]["historical_passed"]
    assert not results["gates"][profile]["forward_proven"]
    assert plan.holdings == 4 and plan.drift_band == .04
    assert plan.research_multiple == 1 and plan.allocation_policy != "legacy"


def test_old_balanced_is_preserved_as_custom_not_silently_upgraded():
    path = Path(__file__).resolve().parents[1]/"docs/reports/balanced-20260930/protocol.json"
    frozen = json.loads(path.read_text(encoding="utf-8"))["cases"]["original"]
    original = {**frozen, "profile": "balanced"}
    restored = Plan.model_validate(original)
    assert restored.profile == "custom"
    assert restored.holdings == 3 and restored.drawdown_pause == .12 and restored.drift_band == .04
    assert fingerprint(restored) == fingerprint(Plan.model_validate(frozen))


def test_evidence_must_match_current_preset_and_not_account(tmp_path):
    store = Store(tmp_path)
    active = save_settings(store, fixed("aggressive"), 1)
    assert current_evidence(store) == {}
    record = {"historical_passed": True, "forward_proven": False, "plan_key": fingerprint(fixed("balanced"))}
    store.set_meta(KEY, {"balanced": record})
    assert current_evidence(store) == {"balanced": record}
    store.set_meta(KEY, {"balanced": {**record, "plan_key": "old-plan"}})
    assert current_evidence(store) == {}
    store.set_meta(KEY, {"balanced": {**record, "historical_passed": False}})
    assert current_evidence(store) == {}
    assert settings(store) == active


@pytest.mark.parametrize("profile", ["conservative", "balanced"])
def test_saved_balanced_finds_prepared_complete_evidence(profile):
    from ai_stock_bot.v4.service import current_report_for
    root = Path(__file__).resolve().parents[1]/"docs/reports/core-profiles-20260930"
    store = Store(root/"production-store")
    plan = fixed(profile, validation_start_year=2020, validation_end_year=2025)
    report = current_report_for(store, plan)
    assert report and report["complete"] and report["plan_key"] == fingerprint(plan)
    assert set(report["results"]) == {"ml", "hold", "hold_full", "stress", "unlevered", "hold_1x"}
    changed = plan.model_copy(update={"drift_band": .06})
    assert current_report_for(store, changed) is None
