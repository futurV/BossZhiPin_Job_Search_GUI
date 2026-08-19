"""Pre-send validation and audit logging for LLM-generated letters.

Every generated letter goes through `validate_letter` before being sent to BOSS.
Failures are logged but not sent, preventing garbage / error strings from
reaching recruiters. Every attempt (sent or not) is appended to a JSONL file
for later review and prompt iteration.
"""

from __future__ import annotations

import csv
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(os.getenv("LETTER_LOG_PATH", "./logs/letters.jsonl"))
APPLICATION_CSV_PATH = Path(
    os.getenv("BOSS_APPLICATION_CSV_PATH", "./logs/applications.csv")
)

APPLICATION_CSV_FIELDS: tuple[str, ...] = (
    "投递时间",
    "状态",
    "求职期望",
    "公司名称",
    "职位名称",
    "薪资",
    "工作地点",
    "经验要求",
    "学历要求",
    "职位标签",
    "招聘者",
    "职位链接",
    "匹配分数",
    "匹配理由",
    "命中关键词",
    "LLM供应商",
    "LLM模型",
    "实际招呼语",
    "职位描述",
)

_APPLICATION_CSV_LOCK = threading.Lock()

MIN_LEN = int(os.getenv("LETTER_MIN_LEN", "30"))
MAX_LEN = int(os.getenv("LETTER_MAX_LEN", "800"))

# Substrings that indicate an LLM error or a refusal — never safe to send.
BLACKLIST: tuple[str, ...] = (
    "Error",
    "Traceback",
    "抱歉，作为",
    "抱歉，我是",
    "I cannot",
    "I apologize",
    "I'm an AI",
    "I'm sorry",
    "As an AI",
    "```",
)

_CJK_RE = re.compile(r"[一-鿿]")


@dataclass
class ValidationResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def validate_letter(letter: str) -> ValidationResult:
    """Return ok=False with reasons if the letter is unsafe to send."""
    reasons: list[str] = []
    n = len(letter)
    if n < MIN_LEN:
        reasons.append(f"too_short ({n} < {MIN_LEN})")
    if n > MAX_LEN:
        reasons.append(f"too_long ({n} > {MAX_LEN})")
    if not _CJK_RE.search(letter):
        reasons.append("no_chinese_characters")
    for needle in BLACKLIST:
        if needle in letter:
            reasons.append(f"blacklist:{needle!r}")
            break
    return ValidationResult(ok=not reasons, reasons=reasons)


def log_attempt(
    *,
    provider: str,
    model: str,
    job_description: str,
    letter: str,
    validation: ValidationResult,
    dry_run: bool,
    sent: bool,
) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "model": model,
        "dry_run": dry_run,
        "validation_ok": validation.ok,
        "validation_reasons": validation.reasons,
        "sent": sent,
        "letter_len": len(letter),
        "job_description": job_description,
        "letter": letter,
    }
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_sent_application(
    *,
    job_metadata: dict,
    job_description: str,
    letter: str,
    provider: str,
    model: str,
    label: str,
    match_score: int | None,
    match_reason: str = "",
    matched_keywords: list[str] | None = None,
) -> bool:
    """把已确认送达的岗位追加到 Excel 兼容 CSV；重复岗位不重复写。

    文件使用 UTF-8 BOM（``utf-8-sig``），中文 Windows Excel 可直接双击打开；
    ``csv`` 标准库负责转义职位描述里的逗号、引号和多行文本。优先用 BOSS 唯一
    职位链接去重，链接缺失时退回“公司 + 职位 + 地点”组合键。

    返回 ``True`` 表示新增一行，``False`` 表示检测到重复并跳过。
    """
    path = APPLICATION_CSV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    job_url = str(job_metadata.get("job_url") or "").strip()
    fallback_key = "|".join(
        str(job_metadata.get(key) or "").strip()
        for key in ("company_name", "job_title", "location")
    )

    with _APPLICATION_CSV_LOCK:
        if path.exists() and path.stat().st_size > 0:
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as f:
                    for old in csv.DictReader(f):
                        old_url = (old.get("职位链接") or "").strip()
                        old_fallback = "|".join(
                            (old.get(key) or "").strip()
                            for key in ("公司名称", "职位名称", "工作地点")
                        )
                        if (job_url and old_url == job_url) or (
                            not job_url and fallback_key and old_fallback == fallback_key
                        ):
                            return False
            except (OSError, csv.Error):
                # CSV 审计不能阻断已经完成的真实发送；坏历史文件仍允许追加，新行
                # 会保留，后续人工修复历史部分即可。
                pass

        is_new = not path.exists() or path.stat().st_size == 0
        row = {
            "投递时间": datetime.now().astimezone().isoformat(timespec="seconds"),
            "状态": "已发送",
            "求职期望": label,
            "公司名称": job_metadata.get("company_name", ""),
            "职位名称": job_metadata.get("job_title", ""),
            "薪资": job_metadata.get("salary", ""),
            "工作地点": job_metadata.get("location", ""),
            "经验要求": job_metadata.get("experience", ""),
            "学历要求": job_metadata.get("education", ""),
            "职位标签": "、".join(job_metadata.get("tags") or []),
            "招聘者": job_metadata.get("recruiter_name", ""),
            "职位链接": job_url,
            "匹配分数": "" if match_score is None else match_score,
            "匹配理由": match_reason,
            "命中关键词": "、".join(matched_keywords or []),
            "LLM供应商": provider,
            "LLM模型": model,
            "实际招呼语": letter,
            "职位描述": job_description,
        }
        with path.open("a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=APPLICATION_CSV_FIELDS)
            if is_new:
                writer.writeheader()
            writer.writerow(row)
        return True
