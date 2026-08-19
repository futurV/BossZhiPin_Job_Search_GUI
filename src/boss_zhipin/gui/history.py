"""GUI 历史面板用——读 ``logs/letters.jsonl``。

``audit.__init__`` 负责写，本模块负责读。分开是因为：
- ``audit`` 是 CLI 也用的业务模块，不应该 import 任何"列表读取/分页"逻辑
- 测试 ``audit.log_attempt`` 不需要测 read
- 未来历史面板想加过滤/分页/全文搜索，集中在这里
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any


def _letters_path() -> Path:
    return Path(os.getenv("LETTER_LOG_PATH", "./logs/letters.jsonl"))


def read_letters(limit: int = 200) -> list[dict[str, Any]]:
    """读 letters.jsonl 最末尾 ``limit`` 条，最新的在 list 末尾。

    文件不存在 / 解析失败的行被跳过——前端面板不会因为一条坏数据整个崩。
    """
    path = _letters_path()
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _applications_path() -> Path:
    return Path(os.getenv("BOSS_APPLICATION_CSV_PATH", "./logs/applications.csv"))


def read_applications(limit: int = 200) -> list[dict[str, Any]]:
    """读取成功投递 CSV，转换成前端稳定的英文键名。"""
    path = _applications_path()
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))[-limit:]
    except (OSError, csv.Error):
        return []
    return [
        {
            "appliedAt": row.get("投递时间", ""),
            "status": row.get("状态", ""),
            "label": row.get("求职期望", ""),
            "companyName": row.get("公司名称", ""),
            "jobTitle": row.get("职位名称", ""),
            "salary": row.get("薪资", ""),
            "location": row.get("工作地点", ""),
            "experience": row.get("经验要求", ""),
            "education": row.get("学历要求", ""),
            "tags": row.get("职位标签", ""),
            "recruiterName": row.get("招聘者", ""),
            "jobUrl": row.get("职位链接", ""),
            "matchScore": row.get("匹配分数", ""),
            "matchReason": row.get("匹配理由", ""),
            "matchedKeywords": row.get("命中关键词", ""),
            "provider": row.get("LLM供应商", ""),
            "model": row.get("LLM模型", ""),
            "greeting": row.get("实际招呼语", ""),
            "jobDescription": row.get("职位描述", ""),
        }
        for row in rows
    ]
