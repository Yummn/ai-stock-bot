"""Append completed execution studies; never silently replace older research."""
import argparse
import copy
from pathlib import Path

from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.research import write_json
from ai_stock_bot.v4.research_report import KEY, execution_publication


def main(args):
    store = Store(args.data_dir)
    before = {k: store.meta(k) for k in ("investment_settings", "portfolio_state", "portfolio_observation")}
    report = copy.deepcopy(store.meta(KEY))
    if not report:
        raise ValueError("Original comparison report is missing")
    updates = [execution_publication(p) for p in args.studies]
    previous = report.get("execution_upgrades", [])
    ids = {r["id"] for r in updates}
    report["execution_upgrades"] = [r for r in previous if r["id"] not in ids] + updates
    for directory, result in zip(args.studies, updates):
        write_json(directory/"publication.json", result)
    store.set_meta(KEY, report)
    assert all(store.meta(k) == v for k, v in before.items())
    print(f"Published {sum(r['replays'] for r in updates)} completed paired replays; settings and observations unchanged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("studies", nargs="+", type=Path)
    main(parser.parse_args())
