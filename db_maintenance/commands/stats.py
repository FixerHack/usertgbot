"""`stats` — reads metrics/db_maintenance.jsonl and summarizes the last N
runs: whether broken-record counts are trending up or down over time.
"""

from db_maintenance.metrics.store import RunMetrics, read_last


def summarize(runs: list[RunMetrics]) -> str:
    if not runs:
        return "No runs recorded yet."

    lines = [f"{'started_at':<26} {'command':<8} {'scanned':>8} {'broken':>7} {'fixed':>6} {'deleted':>8} {'sec':>7}"]
    for r in runs:
        lines.append(
            f"{r.started_at:<26} {r.command:<8} {r.scanned:>8} {r.broken:>7} {r.fixed:>6} {r.deleted:>8} "
            f"{r.duration_seconds:>7.2f}"
        )

    broken_series = [r.broken for r in runs if r.command == "check"]
    if len(broken_series) >= 2:
        delta = broken_series[-1] - broken_series[0]
        if delta > 0:
            trend = f"trend: broken records UP by {delta} over {len(broken_series)} check run(s)"
        elif delta < 0:
            trend = f"trend: broken records DOWN by {-delta} over {len(broken_series)} check run(s)"
        else:
            trend = f"trend: broken records flat over {len(broken_series)} check run(s)"
        lines.append("")
        lines.append(trend)

    return "\n".join(lines)


def run_stats(n: int) -> str:
    return summarize(read_last(n))
