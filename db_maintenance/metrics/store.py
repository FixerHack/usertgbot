"""Append-only JSONL metrics log for each db_maintenance run.

No external metrics backend (Prometheus/Grafana) — just a local file that
`stats` reads back to show trends across runs.
"""

import json
from dataclasses import asdict, dataclass

from db_maintenance.config import settings


@dataclass
class RunMetrics:
    started_at: str
    command: str
    duration_seconds: float
    scanned: int
    broken: int
    fixed: int
    deleted: int
    source: str = "postgres"


def append(run: RunMetrics) -> None:
    settings.metrics_file.parent.mkdir(parents=True, exist_ok=True)
    with settings.metrics_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(run)) + "\n")


def read_last(n: int) -> list[RunMetrics]:
    if not settings.metrics_file.exists():
        return []
    lines = settings.metrics_file.read_text(encoding="utf-8").splitlines()
    return [RunMetrics(**json.loads(line)) for line in lines[-n:]]
