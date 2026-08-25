from __future__ import annotations

import math

import pytest

from boss_zhipin.salary import is_below_minimum_salary, parse_monthly_salary_k


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("15-25K·13薪", (15.0, 25.0)),
        ("8000-12000元/月", (8.0, 12.0)),
        ("20-30万/年", (20 * 10 / 12, 30 * 10 / 12)),
        ("200-300元/天", (4.35, 6.525)),
        ("50-80元/时", (8.7, 13.92)),
    ],
)
def test_parse_salary_formats(text, expected):
    assert parse_monthly_salary_k(text) == pytest.approx(expected)


def test_open_ended_and_unknown_salary():
    assert parse_monthly_salary_k("20K以上") == (20.0, math.inf)
    assert parse_monthly_salary_k("面议") is None
    assert parse_monthly_salary_k("") is None


def test_minimum_salary_uses_job_upper_bound():
    assert is_below_minimum_salary("15-25K", 20)[0] is False
    assert is_below_minimum_salary("10-15K", 20)[0] is True
    assert is_below_minimum_salary("面议", 20)[0] is False
