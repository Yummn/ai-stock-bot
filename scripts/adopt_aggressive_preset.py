"""Label the already-installed research plan without changing trading parameters."""
import argparse
import json
from pathlib import Path

from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.schema import FIXED_PROFILE_PRESETS, Plan
from ai_stock_bot.v4.service import settings, save_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    store = Store(args.data_dir)
    current = settings(store)
    before = Plan.model_validate(current["plan"])
    preset = FIXED_PROFILE_PRESETS["aggressive"]
    differing = [key for key, value in preset.items() if key != "label" and getattr(before, key) != value]
    if differing:
        raise ValueError(f"Current settings differ; refusing to overwrite: {differing}")
    if before.profile == "aggressive" and before.preset_version == 1:
        print("Already adopted")
        return
    backup = store.root/f"settings-before-fixed-presets-r{current['revision']}.json"
    with backup.open("x", encoding="utf-8") as stream:
        json.dump(current, stream, ensure_ascii=False, indent=2)
    plan = Plan.model_validate({**before.model_dump(), "profile": "aggressive", "preset_version": 1})
    saved = save_settings(store, plan, current["revision"])
    print(f"Saved revision {saved['revision']}; only profile metadata changed; historical evidence retained.")


if __name__ == "__main__":
    main()
