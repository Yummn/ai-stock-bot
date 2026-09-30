from collections import Counter
from datetime import datetime, timedelta


def clocks(interval):
    return (["10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"]
            if interval == "30m" else ["10:30", "11:30", "14:00", "15:00"])


def continuous(rows, interval):
    schedule = clocks(interval)
    for row in rows:
        stamp = datetime.fromisoformat(row["stamp"])
        if stamp.strftime("%H:%M") not in schedule or stamp.second or stamp.microsecond:
            return False
    for a, b in zip(rows, rows[1:]):
        if a["stamp"][:10] == b["stamp"][:10]:
            if schedule.index(b["stamp"][11:16]) != schedule.index(a["stamp"][11:16]) + 1:
                return False
        elif a["stamp"][11:16] != schedule[-1] or b["stamp"][11:16] != schedule[0]:
            return False
    return True


def quality(rows, interval):
    schedule = clocks(interval)
    counts = Counter(row["stamp"][:10] for row in rows)
    observed = {day: {r["stamp"][11:16] for r in rows if r["stamp"].startswith(day)} for day in counts}
    missing = [{"date": day, "clocks": [clock for clock in schedule if clock not in found]}
               for day, found in observed.items()]
    missing = [item for item in missing if item["clocks"]]
    expected = len(counts) * len(schedule)
    return {"expected_bars_on_observed_days": expected, "bars": len(rows),
            "missing_bars": sum(len(item["clocks"]) for item in missing),
            "coverage_ratio": len(rows) / expected if expected else 0.,
            "incomplete_sessions": len(missing), "missing": missing,
            "invalid_clock_count": sum(row["stamp"][11:16] not in schedule for row in rows),
            "note": "按已出现的交易日检查完整时段；不填补缺失 K 线。尚未核验整日缺失、公司行动和逐笔成交。"}


def first_executable(rows, index, interval, delay=10):
    minutes = 30 if interval == "30m" else 60
    issued = datetime.fromisoformat(rows[index]["stamp"]) + timedelta(minutes=delay)
    for next_index in range(index + 1, len(rows)):
        opening = datetime.fromisoformat(rows[next_index]["stamp"]) - timedelta(minutes=minutes)
        if opening >= issued:
            return next_index
    return None
