import pytest

from eval.stats import episodes, poisson_upper, proportion_summary, rate_summary, wilson


def test_wilson_matches_plan_example():
    lo, hi = wilson(10, 10)  # plan section 4: "10/10 only supports 72-100%"
    assert lo == pytest.approx(0.722, abs=1e-3) and hi == pytest.approx(1.0)
    lo, hi = wilson(0, 50)
    assert lo == 0 and hi == pytest.approx(0.0713, abs=1e-3)
    assert wilson(0, 0) == (0.0, 1.0)


def test_poisson_upper_rule_of_three():
    assert poisson_upper(0, 1.0) == pytest.approx(2.996, abs=1e-3)
    assert poisson_upper(0, 12.54) == pytest.approx(3 / 12.54, rel=0.01)
    assert poisson_upper(5, 10.0) > 0.5  # bound sits above the point estimate


def test_summaries_and_episodes():
    r = rate_summary(3, 12.0)
    assert r["per_hour"] == 0.25 and r["upper95_per_hour"] > 0.25
    p = proportion_summary(7, 10)
    assert p["rate"] == 0.7 and p["wilson95"][0] < 0.7 < p["wilson95"][1]
    assert episodes([0, 1, 1, 0, 1, 0, 0, 1]) == 3
    assert episodes([]) == 0
