import math

import pytest

from movbench.scoring import elo_ratings, score, summarize
from movbench.storage import leaderboard


def test_score_rewards_validity_and_each_speed_doubling():
    assert score(False, 1, 1000) == 0
    assert score(True, 200, 100) == 100
    assert score(True, 100, 100) == 100
    assert score(True, 50, 100) == 125
    assert score(True, 25, 100) == 150
    assert score(True) == 100
    with pytest.raises(ValueError):
        score(True, math.nan, 100)


def test_sample_summary_retains_observations_and_resists_outlier():
    result = summarize([9, 10, 11, 1000, 10])
    assert result["median_ns"] == 10
    assert result["mad_ns"] == 1
    assert result["samples_ns"][-2] == 1000


def cell(model, task="add_u8", valid=True, time=10):
    return {
        "model": model,
        "task": task,
        "tier": "tiny",
        "valid": valid,
        "score": score(valid, time, 10),
        "timing": {"median_ns": time},
        "speedup": 10 / time,
    }


def test_elo_is_order_independent_and_treats_near_times_as_draws():
    cells = [cell("fast", time=1), cell("slow", time=10)]
    assert elo_ratings(cells) == elo_ratings(list(reversed(cells)))
    assert elo_ratings(cells)["fast"] > 1500 > elo_ratings(cells)["slow"]
    assert elo_ratings([cell("a", time=10), cell("b", time=10.2)]) == {"a": 1500, "b": 1500}


def test_elo_validity_beats_speed_and_missing_cells_are_invalid():
    ratings = elo_ratings([cell("valid", time=100), cell("invalid", valid=False, time=1)])
    assert ratings["valid"] > ratings["invalid"]
    ratings = elo_ratings(
        [cell("complete", task="one"), cell("complete", task="two"), cell("partial", task="one")]
    )
    assert ratings["complete"] > ratings["partial"]


def fake_run(id, model, cohort, cells):
    return {
        "id": id,
        "model": model,
        "cohort": cohort,
        "created": id,
        "hardware": {"cpu": "test", "target": "zen5", "target_verified": True},
        "tasks": ["add_u8", "mul_u8"],
        "tiers": ["tiny"],
        "settings": {"repeats": 7, "trial_ms": 10},
        "results": cells,
    }


def test_leaderboard_latest_model_cohort_isolation_and_invalid_denominator():
    runs = [
        fake_run("3", "same", "a", [cell("same"), cell("same", "mul_u8", False)]),
        fake_run("2", "same", "a", [cell("same", time=1)]),
        fake_run("1", "other-hardware", "b", [cell("other-hardware", time=0.1)]),
    ]
    data = leaderboard(runs, "a")
    assert len(data["rows"]) == 1
    assert data["rows"][0]["run_id"] == "3"
    assert data["rows"][0]["score"] == 50
    assert data["rows"][0]["valid"] == 1
    assert data["rows"][0]["total"] == 2
