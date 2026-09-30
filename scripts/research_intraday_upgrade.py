"""Bounded chronological intraday research; never changes the saved plan/account."""
import argparse
import gzip
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ai_stock_bot.v2.store import Store
from ai_stock_bot.v4.data import execution_interval, refresh_intraday
from ai_stock_bot.v4.intraday import research
from ai_stock_bot.v4.intraday_forecast import ForecastTape
from ai_stock_bot.v4.schema import ASSETS, ENGINE_VERSION, Plan
from ai_stock_bot.v4.service import settings


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--refresh", action="store_true")
    source.add_argument("--snapshot", type=Path, help="Replay an earlier market.json.gz and its saved plans")
    parser.add_argument("--multiples", type=int, nargs="+", choices=(1, 2, 3), default=[1, 2, 3])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    store = Store(root / "data" / "v4")
    plan = Plan.model_validate(settings(store)["plan"])
    temporary = None
    if args.snapshot:
        from ai_stock_bot.v4.data import intraday_table
        previous = json.loads((args.snapshot.parent / "results.json").read_text(encoding="utf-8"))
        plan = Plan.model_validate(previous["results"][0]["plan"])
        with gzip.open(args.snapshot, "rt", encoding="utf-8") as stream:
            frozen = json.load(stream)
        temporary = tempfile.TemporaryDirectory(prefix="stocklab-intraday-replay-")
        store = Store(Path(temporary.name))
        intraday_table(store)
        with store.connect() as connection:
            connection.executemany("INSERT INTO intraday_bars VALUES(?,?,?,?,?,?,?,?,?)", [
                tuple(row[key] for key in ("symbol", "stamp", "interval", "open", "high", "low", "close", "volume", "source"))
                for rows in frozen.values() for row in rows])
    symbols = [s for s, asset in ASSETS.items() if asset["t0"]]
    output = root / "docs" / "reports" / ("intraday-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=False)
    results = []
    snapshot = {}
    for interval in ("30m", "60m"):
        if args.refresh:
            refresh_intraday(store, symbols, lambda *message: print(*message, flush=True), interval)
        with store.connect() as connection:
            snapshot[interval] = [dict(r) for r in connection.execute(
                "SELECT * FROM intraday_bars WHERE interval=? ORDER BY symbol,stamp", (execution_interval(interval),))]
        for symbol in symbols:
            rows = [{key: value for key, value in r.items() if key not in {"symbol", "interval"}}
                    for r in snapshot[interval] if r["symbol"] == symbol]
            print(f"Training frozen snapshot: {symbol} / {interval}", flush=True)
            forecast = ForecastTape(rows, plan.model_copy(update={"research_interval": interval}))
            for multiple in args.multiples:
                case = plan.model_copy(update={"research_interval": interval, "research_symbol": symbol,
                                                "research_multiple": multiple})
                report = research(store, case, symbol, case.research_annual_rate, forecast=forecast)
                results.append({"plan": case.model_dump(), "report": report})
                v = report.get("validation", {})
                print(json.dumps({"interval": interval, "symbol": symbol, "multiple": multiple,
                                  "sessions": report["coverage"]["sessions"], "selected": v.get("selected"),
                                  "status": v.get("status"), "current": v.get("current", {}).get("liquidation_return"),
                                  "tested": v.get("evaluation", {}).get("liquidation_return"),
                                  "legacy": v.get("legacy", {}).get("liquidation_return"),
                                  "hold": v.get("hold", {}).get("liquidation_return")}), flush=True)
    with gzip.open(output / "market.json.gz", "wt", encoding="utf-8") as stream:
        json.dump(snapshot, stream, ensure_ascii=False)
    implementation = hashlib.sha256()
    for filename in ("intraday.py", "intraday_engine.py", "intraday_forecast.py", "intraday_quality.py",
                     "hourly_paper.py", "schema.py", "data.py", "../v2/execution.py"):
        implementation.update((root / "src" / "ai_stock_bot" / "v4" / filename).read_bytes())
    payload = {"engine": ENGINE_VERSION, "implementation_sha256": implementation.hexdigest(),
               "created_at": datetime.now(timezone.utc).isoformat(),
               "saved_plan_unchanged": True, "results": results}
    (output / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Intraday Upgrade Evaluation", "", "Three fixed candidates; 65% chronological development, 35% later evaluation.",
             "Same dataset and costs, 100-share lots, 10-minute decision delay, subsequent open, calendar-day financing, volume caps.",
             "History has been viewed before. This is not pristine out-of-sample evidence.",
             "No candidate is automatically applied; no real orders are submitted.", "",
             "| Interval | Symbol | Multiple | Selected on development | Later net* | Original net* | Hold net* | Status |",
             "|---|---|---|---|---|---|---|---|"]
    for result in results:
        p, r = result["plan"], result["report"]
        v = r.get("validation", {})
        if "evaluation" in v:
            lines.append(f"| {p['research_interval']} | {r['symbol']} | {p['research_multiple']}x | {v['selected']} | "
                         f"{v['evaluation']['liquidation_return']:.2%} | {v['legacy']['liquidation_return']:.2%} | "
                         f"{v['hold']['liquidation_return']:.2%} | {v['status']} |")
    lines += ["", "* Includes estimated liquidation costs for any remaining position.",
              "Short samples cannot establish a dependable profit edge, especially under leverage.",
              "Snapshot and full parameters/results are saved alongside this report."]
    lines += ["", "## Fixed-strategy forward slices", "",
              "30 observed sessions for initial warmup, then separate ten-session accounts. No automatic parameter promotion.",
              "Means below are means of short independent period returns, NOT annualized or total account returns.", "",
              "| Interval | Symbol | Multiple | Strategy | Mean net | Mean stressed | Positive stressed periods | Trades | Data coverage |",
              "|---|---|---|---|---|---|---|---|---|"]
    for item in results:
        p, r = item["plan"], item["report"]
        for s in r.get("walk_forward", {}).get("strategies", []):
            lines.append(f"| {p['research_interval']} | {r['symbol']} | {p['research_multiple']}x | {s['strategy']} | "
                         f"{s['mean_return']:.2%} | {s['mean_stress_return']:.2%} | "
                         f"{s['positive_stress_periods']}/{len(s['periods'])} | "
                         f"{sum(f['base']['trade_count'] for f in s['periods'])} | {r['coverage']['quality']['coverage_ratio']:.2%} |")
    (output / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(str(output), flush=True)
    if temporary:
        temporary.cleanup()


if __name__ == "__main__":
    main()
