import math
import statistics

BASE_POINTS = 100.0
BONUS_PER_DOUBLING = 25.0


def score(valid: bool, candidate_ns=None, reference_ns=None):
    if not valid:
        return 0.0
    if candidate_ns is None or reference_ns is None:
        return BASE_POINTS
    if not all(math.isfinite(x) and x > 0 for x in (candidate_ns, reference_ns)):
        raise ValueError("Timings must be finite positive ns/kernel values")
    return BASE_POINTS + BONUS_PER_DOUBLING * max(0.0, math.log2(reference_ns / candidate_ns))


def summarize(samples):
    if len(samples) < 3 or not all(math.isfinite(x) and x > 0 for x in samples):
        raise ValueError("Need at least three positive timing samples")
    median = statistics.median(samples)
    return {
        "median_ns": median,
        "mad_ns": statistics.median(abs(x - median) for x in samples),
        "samples_ns": samples,
        "repetitions": len(samples),
    }


def elo_ratings(results: list[dict], rounds: int = 16, k: float = 16.0, tie_fraction=0.03):
    """Deterministic descriptive Elo, only within a supplied comparable cohort.

    Each pair of models plays each shared task/tier once per round. Batched
    updates avoid ordering bias; times within 3% are draws. Invalid beats invalid
    as a draw, valid beats invalid. Missing cells count as invalid.
    """
    models = sorted({r["model"] for r in results})
    ratings = {m: 1500.0 for m in models}
    cells = {(r["model"], r["task"], r["tier"]): r for r in results}
    keys = sorted({(r["task"], r["tier"]) for r in results})
    for _ in range(rounds):
        deltas = {m: [] for m in models}
        for i, a in enumerate(models):
            for b in models[i + 1 :]:
                outcomes = []
                for task, tier in keys:
                    x, y = cells.get((a, task, tier), {}), cells.get((b, task, tier), {})
                    xv, yv = x.get("valid", False), y.get("valid", False)
                    if xv != yv:
                        outcome = float(xv)
                    elif not xv:
                        outcome = 0.5
                    else:
                        xt, yt = x["timing"]["median_ns"], y["timing"]["median_ns"]
                        outcome = (
                            0.5 if max(xt, yt) / min(xt, yt) <= 1 + tie_fraction else float(xt < yt)
                        )
                    outcomes.append(outcome)
                if outcomes:
                    expected = 1 / (1 + 10 ** ((ratings[b] - ratings[a]) / 400))
                    delta = k * (statistics.mean(outcomes) - expected)
                    deltas[a].append(delta)
                    deltas[b].append(-delta)
        for m in models:
            ratings[m] += statistics.mean(deltas[m]) if deltas[m] else 0
    return ratings
