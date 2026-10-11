import json
import sqlite3
import statistics
from pathlib import Path

from .scoring import elo_ratings


def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created TEXT NOT NULL, "
        "model TEXT NOT NULL, cohort TEXT NOT NULL, payload TEXT NOT NULL)"
    )
    conn.commit()
    return conn


def save_run(path: Path, run: dict):
    with connect(path) as db:
        db.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
            (run["id"], run["created"], run["model"], run["cohort"], json.dumps(run)),
        )


def get_runs(path: Path):
    with connect(path) as db:
        return [
            json.loads(row[0])
            for row in db.execute("SELECT payload FROM runs ORDER BY created DESC, id DESC")
        ]


def leaderboard(runs, cohort=None, tier=None, task=None, query=""):
    cohorts = []
    for run in runs:
        if run["cohort"] not in {c["id"] for c in cohorts}:
            cohorts.append(
                {
                    "id": run["cohort"],
                    "cpu": run["hardware"]["cpu"],
                    "target": run["hardware"]["target"],
                    "target_verified": run["hardware"]["target_verified"],
                    "tasks": run["tasks"],
                    "tiers": run["tiers"],
                    "repeats": run["settings"]["repeats"],
                    "trial_ms": run["settings"]["trial_ms"],
                }
            )
    cohort = cohort or (cohorts[0]["id"] if cohorts else None)
    selected, seen = [], set()
    for run in runs:
        if run["cohort"] == cohort and run["model"] not in seen:
            seen.add(run["model"])
            selected.append(run)
    ratings_input, rows = [], []
    for run in selected:
        cells = [
            r
            for r in run["results"]
            if (not tier or r["tier"] == tier) and (not task or r["task"] == task)
        ]
        ratings_input += [{**r, "model": run["model"]} for r in cells]
        if not cells or query.casefold() not in run["model"].casefold():
            continue
        valid = [r for r in cells if r["valid"]]
        speedups = [r["speedup"] for r in valid]
        rows.append(
            {
                "model": run["model"],
                "run_id": run["id"],
                "created": run["created"],
                "score": statistics.mean(r["score"] for r in cells),
                "valid": len(valid),
                "total": len(cells),
                "geomean_speedup": statistics.geometric_mean(speedups) if speedups else None,
                "memory_bytes": max((r.get("memory_bytes", 0) for r in valid), default=0),
                "results": cells,
                "metadata": run.get("metadata", {}),
            }
        )
    ratings = elo_ratings(ratings_input)
    for row in rows:
        row["elo"] = ratings[row["model"]]
    rows.sort(key=lambda r: (-r["score"], r["model"]))
    return {
        "cohort": cohort,
        "cohorts": cohorts,
        "rows": rows,
        "total_runs": len(runs),
        "scoring": "100 + 25 × max(0, log₂(reference_ns / candidate_ns))",
    }
