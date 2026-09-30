"""Independent strategy slots; account inputs remain shared and activation explicit."""
import copy
import json

from .model_recipes import RECIPE_LABELS
from .schema import FIXED_PROFILE_PRESETS, METHODS, Plan, fingerprint

KEY = "investment_profile_library"
SLOTS = ("conservative", "balanced", "aggressive", "custom")
SHARED_FIELDS = frozenset({"name", "starting_cash", "auto_update", "validation_start_year", "validation_end_year",
    "commission_rate", "min_commission", "slippage_bps", "participation", "research_annual_rate", "research_maintenance_ratio"})


def signal_name(plan):
    if plan.method == "ml" and plan.model_recipe != "legacy":
        return RECIPE_LABELS[plan.model_recipe]
    return METHODS[plan.method]


def active_slot(current):
    return current.get("profile_slot") or current["plan"].get("profile", "custom")


def strategy(plan):
    return plan.model_dump(exclude=SHARED_FIELDS)


def initial_entries(current):
    entries = {}
    for slot, preset in FIXED_PROFILE_PRESETS.items():
        plan = Plan(**{k: v for k, v in preset.items() if k != "label"}, profile=slot, preset_version=1)
        entries[slot] = strategy(plan)
    entries["custom"] = strategy(Plan.model_validate({**current["plan"], "profile": "custom"}))
    # Existing active settings survive a change to recommended defaults.
    slot = active_slot(current)
    if slot in SLOTS:
        entries[slot] = strategy(Plan.model_validate(current["plan"]))
    return entries


def record_for(current, stored=None):
    entries = initial_entries(current)
    if stored:
        entries.update({k: copy.deepcopy(v) for k, v in stored.get("entries", {}).items() if k in SLOTS})
    return {"version": 1, "entries": entries}


def view(store, current):
    record = record_for(current, store.meta(KEY))
    shared = {k: current["plan"][k] for k in SHARED_FIELDS}
    entries = {}
    for slot, saved in record["entries"].items():
        plan = Plan.model_validate({**saved, **shared})
        reference = FIXED_PROFILE_PRESETS.get(slot)
        modified = not reference or any(getattr(plan, k) != v for k, v in reference.items()
                                       if k != "label" and k not in SHARED_FIELDS)
        entries[slot] = {"plan": plan.model_dump(), "plan_key": fingerprint(plan),
                         "modified": modified, "signal_name": signal_name(plan)}
    return {"active_slot": active_slot(current), "entries": entries, "shared_fields": sorted(SHARED_FIELDS)}


def save_in_transaction(connection, current, plan, slot):
    if slot not in SLOTS:
        raise ValueError("未知策略档位")
    row = connection.execute("SELECT value FROM meta WHERE key=?", (KEY,)).fetchone()
    value = record_for(current, json.loads(row[0]) if row else None)
    value["entries"][slot] = strategy(plan)
    connection.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (KEY, json.dumps(value, ensure_ascii=False, allow_nan=False)))
