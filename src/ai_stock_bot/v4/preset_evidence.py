"""Small, version-matched research receipts, separate from account validation."""
from .schema import FIXED_PROFILE_PRESETS, Plan, fingerprint

KEY = "portfolio_preset_evidence"


def current_evidence(store):
    records = store.meta(KEY) or {}
    matched = {}
    for profile, record in records.items():
        if profile not in FIXED_PROFILE_PRESETS or not record.get("historical_passed"):
            continue
        preset = {k: v for k, v in FIXED_PROFILE_PRESETS[profile].items() if k != "label"}
        plan = Plan(**preset, profile=profile, preset_version=1)
        if fingerprint(plan) == record.get("plan_key"):
            matched[profile] = record
    return matched
