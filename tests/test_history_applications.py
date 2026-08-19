from __future__ import annotations

import csv

from boss_zhipin.gui import history


def test_read_applications_maps_csv_fields(tmp_path, monkeypatch):
    path = tmp_path / "applications.csv"
    monkeypatch.setenv("BOSS_APPLICATION_CSV_PATH", str(path))
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "投递时间", "状态", "求职期望", "公司名称", "职位名称", "薪资",
                "工作地点", "经验要求", "学历要求", "职位标签", "招聘者", "职位链接",
                "匹配分数", "匹配理由", "命中关键词", "LLM供应商", "LLM模型",
                "实际招呼语", "职位描述",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "投递时间": "2026-08-12T16:00:00+08:00",
                "公司名称": "测试公司",
                "职位名称": "AI Agent 工程师",
                "匹配分数": "88",
                "实际招呼语": "您好",
                "职位描述": "完整 JD",
            }
        )

    rows = history.read_applications()
    assert rows == [
        {
            "appliedAt": "2026-08-12T16:00:00+08:00",
            "status": "",
            "label": "",
            "companyName": "测试公司",
            "jobTitle": "AI Agent 工程师",
            "salary": "",
            "location": "",
            "experience": "",
            "education": "",
            "tags": "",
            "recruiterName": "",
            "jobUrl": "",
            "matchScore": "88",
            "matchReason": "",
            "matchedKeywords": "",
            "provider": "",
            "model": "",
            "greeting": "您好",
            "jobDescription": "完整 JD",
        }
    ]
