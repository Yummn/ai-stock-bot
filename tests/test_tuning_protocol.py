import copy
import importlib.util
from pathlib import Path

import pytest

from ai_stock_bot.v2.schema import AppConfig, MODEL_NAMES

spec = importlib.util.spec_from_file_location("tune_stocklab", Path(__file__).parents[1] / "scripts" / "tune_stocklab.py")
tuning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tuning)


def test_manual_tuning_preserves_non_ml_and_custom_settings():
    before = AppConfig().model_dump()
    after = tuning.manual_adjustments(before)
    AppConfig.model_validate(after)
    for key in ("risk", "local", "ai", "symbols", "training_symbols", "annual_labels"):
        assert before[key] == after[key]
    for model in MODEL_NAMES:
        assert before["models"][model]["custom"] == after["models"][model]["custom"]
        assert after["models"][model]["balanced"]["training_mode"] == "mixed"


def test_audit_requires_identical_data_complete_years_and_no_fallback():
    result = {"audit": {profile: [{"year": year, "source": source, "start": f"{year}-01-04", "end": f"{year}-12-31", "bars": 242, "data_hash": str(year), "fallback_count": 0} for year in tuning.AUDIT_YEARS for source in ("baseline", "selected")] for profile in tuning.PRESETS}}
    tuning.verify_audit(result)
    changed = copy.deepcopy(result)
    changed["audit"]["aggressive"][1]["data_hash"] = "different"
    with pytest.raises(RuntimeError, match="different market"):
        tuning.verify_audit(changed)
    changed = copy.deepcopy(result)
    changed["audit"]["balanced"].pop()
    with pytest.raises(RuntimeError, match="complete later-year"):
        tuning.verify_audit(changed)
    changed = copy.deepcopy(result)
    changed["audit"]["conservative"][1]["fallback_count"] = 1
    with pytest.raises(RuntimeError, match="fallback"):
        tuning.verify_audit(changed)
