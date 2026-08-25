"""BOSS 岗位薪资文本解析与最低月薪筛选。"""

from __future__ import annotations

import math
import re

_RANGE = r"(\d+(?:\.\d+)?)\s*[-—–~～至]\s*(\d+(?:\.\d+)?)"


def parse_monthly_salary_k(text: str) -> tuple[float, float] | None:
    """把常见 BOSS 薪资格式换算为月薪区间（单位 K）。

    支持 ``15-25K·13薪``、``8000-12000元/月``、``20-30万/年``、
    ``200-300元/天`` 和 ``50-80元/时``。``面议`` 或未知格式返回 ``None``。
    额外月份（如 13 薪）不摊入月薪，保持与页面月薪口径一致。
    """
    value = (text or "").strip().replace(",", "")
    if not value or "面议" in value:
        return None

    def match_range(unit_pattern: str, factor: float) -> tuple[float, float] | None:
        matched = re.search(_RANGE + unit_pattern, value, flags=re.IGNORECASE)
        if not matched:
            return None
        low, high = float(matched.group(1)), float(matched.group(2))
        return min(low, high) * factor, max(low, high) * factor

    # BOSS 最常见格式；后面的 ·13薪 不影响这里的月薪范围。
    parsed = match_range(r"\s*[Kk](?:\b|·|薪|$)", 1.0)
    if parsed:
        return parsed

    parsed = match_range(r"\s*元\s*/?\s*月", 1 / 1000)
    if parsed:
        return parsed

    parsed = match_range(r"\s*万\s*/?\s*年", 10 / 12)
    if parsed:
        return parsed

    parsed = match_range(r"\s*元\s*/?\s*(?:天|日)", 21.75 / 1000)
    if parsed:
        return parsed

    parsed = match_range(r"\s*元\s*/?\s*(?:时|小时)", 174 / 1000)
    if parsed:
        return parsed

    lower = re.search(r"(\d+(?:\.\d+)?)\s*[Kk]\s*(?:以上|起)", value)
    if lower:
        return float(lower.group(1)), math.inf

    upper = re.search(r"(\d+(?:\.\d+)?)\s*[Kk]\s*以下", value)
    if upper:
        return 0.0, float(upper.group(1))

    single = re.search(r"(\d+(?:\.\d+)?)\s*[Kk](?:\b|·|薪|$)", value)
    if single:
        amount = float(single.group(1))
        return amount, amount

    return None


def is_below_minimum_salary(text: str, minimum_k: float) -> tuple[bool, tuple[float, float] | None]:
    """仅当已解析区间的上限仍低于期望值时返回需要跳过。"""
    parsed = parse_monthly_salary_k(text)
    if minimum_k <= 0 or parsed is None:
        return False, parsed
    return parsed[1] < minimum_k, parsed
