"""write_response 主循环的浏览器控制流回归测试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from boss_zhipin.website_oper import write_response


LETTER = "您好，我对这个岗位很感兴趣，也具备相关项目经验，期待进一步沟通。"


@dataclass
class BrowserPatch:
    sleeps: list[float]
    events: list[tuple[str, dict]]


def _patch_common_browser(monkeypatch) -> BrowserPatch:
    sleeps: list[float] = []
    events: list[tuple[str, dict]] = []
    async def noop(*args, **kwargs):
        return None

    async def fake_sleep(delay: float):
        sleeps.append(delay)

    async def cards_ready(timeout: float = 30):
        return True

    async def contact_state():
        text = await write_response.finding_jobs.get_text_by_css(
            ".op-btn.op-btn-chat"
        )
        return {"found": bool(text), "text": text or "", "disabled": False}

    monkeypatch.setattr(write_response.finding_jobs, "open_browser_with_options", noop)
    monkeypatch.setattr(write_response.finding_jobs, "log_in", noop)
    monkeypatch.setattr(write_response.finding_jobs, "select_dropdown_option", noop)
    # 主循环开跑前会等岗位卡真正渲染出来；这里直接放行，各用例只关心之后的控制流
    monkeypatch.setattr(
        write_response.finding_jobs, "wait_for_real_job_cards", cards_ready
    )
    monkeypatch.setattr(
        write_response.finding_jobs, "get_contact_button_state", contact_state
    )
    monkeypatch.setattr(
        write_response.finding_jobs,
        "get_job_metadata_by_index",
        lambda index: _metadata(index),
    )
    monkeypatch.setattr(write_response.finding_jobs, "reload_page", noop)
    monkeypatch.setattr(write_response.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        write_response,
        "_emit_progress",
        lambda kind, **payload: events.append((kind, payload)),
    )
    monkeypatch.setattr(write_response, "current_provider_label", lambda: "test")
    monkeypatch.setattr(write_response, "log_attempt", lambda **kwargs: None)
    # 主循环测试不能污染真实 logs/applications.csv；CSV 本身的写入/去重由
    # tests/test_audit.py::TestApplicationCsv 在 tmp_path 中单独覆盖。
    monkeypatch.setattr(write_response, "log_sent_application", lambda **kwargs: True)

    return BrowserPatch(sleeps=sleeps, events=events)


def test_missing_job_scrolls_and_retries_same_index(monkeypatch):
    async def scenario():
        patch = _patch_common_browser(monkeypatch)
        requested: list[int] = []
        scrolls: list[bool] = []

        async def get_jd(index: int):
            requested.append(index)
            if index == 1:
                return "岗位1 JD Go Redis 后端开发"
            if index == 2 and len([i for i in requested if i == 2]) == 1:
                return None
            if index == 2:
                return "岗位2 JD Python AI 应用开发"
            return None

        async def get_text(selector: str, timeout: float = 5):
            return "继续沟通"

        async def loaded_count():
            return 1

        async def scroll_more():
            scrolls.append(True)
            return len(scrolls) == 1

        monkeypatch.setattr(
            write_response.finding_jobs, "get_job_description_by_index", get_jd
        )
        monkeypatch.setattr(write_response.finding_jobs, "get_text_by_css", get_text)
        monkeypatch.setattr(
            write_response.finding_jobs, "get_loaded_job_count", loaded_count
        )
        monkeypatch.setattr(
            write_response.finding_jobs, "scroll_to_load_more_jobs", scroll_more
        )

        await write_response.send_job_descriptions_to_chat(
            usr_name="测试",
            url="https://example.test",
            browser_type="chrome",
            label="",
            dry_run=True,
        )

        assert requested[:3] == [1, 2, 2]
        assert scrolls
        assert any(
            kind == "job_found" and payload["index"] == 2
            for kind, payload in patch.events
        )

    asyncio.run(scenario())


def test_never_rendered_job_list_reports_error_not_feed_exhausted(monkeypatch):
    """岗位卡始终没渲染出来时，必须报 error 收尾，不能误报「已到 feed 底部」。

    回归的是这个真实故障：profile 缓存里存了 SPA 脚本的错误响应，页面死在
    「加载中，请稍候」，骨架屏空卡却点得动 —— 老逻辑会空转 5 轮然后上报
    feed_exhausted，让用户以为正常跑完了。
    """

    async def scenario():
        patch = _patch_common_browser(monkeypatch)
        reloads: list[bool] = []
        requested: list[int] = []

        async def never_ready(timeout: float = 30):
            return False

        async def reload():
            reloads.append(True)

        async def get_jd(index: int):
            requested.append(index)
            return None

        monkeypatch.setattr(
            write_response.finding_jobs, "wait_for_real_job_cards", never_ready
        )
        monkeypatch.setattr(write_response.finding_jobs, "reload_page", reload)
        monkeypatch.setattr(
            write_response.finding_jobs, "get_job_description_by_index", get_jd
        )

        await write_response.send_job_descriptions_to_chat(
            usr_name="测试",
            url="https://example.test",
            browser_type="chrome",
            label="",
            dry_run=True,
        )

        kinds = [kind for kind, _ in patch.events]
        assert "error" in kinds
        assert "feed_exhausted" not in kinds
        # 直接收尾，一轮岗位都不该去抓
        assert requested == []
        # 失败前刷新过一次
        assert reloads == [True]

    asyncio.run(scenario())


def test_reload_reapplies_label_filter(monkeypatch):
    """刷新会冲掉客户端筛选状态，必须重新选一次 label。

    否则用户选的 tag 悄悄退化成 BOSS 默认推荐 feed，招呼语会发给一批他没打算投的岗位。
    """

    async def scenario():
        _patch_common_browser(monkeypatch)
        selected: list[str] = []
        ready_calls: list[int] = []

        async def select_label(label: str):
            selected.append(label)

        async def ready_on_second_try(timeout: float = 30):
            ready_calls.append(1)
            return len(ready_calls) > 1

        async def get_jd(index: int):
            return None

        monkeypatch.setattr(
            write_response.finding_jobs, "select_dropdown_option", select_label
        )
        monkeypatch.setattr(
            write_response.finding_jobs, "wait_for_real_job_cards", ready_on_second_try
        )
        monkeypatch.setattr(
            write_response.finding_jobs, "get_job_description_by_index", get_jd
        )
        monkeypatch.setattr(
            write_response.finding_jobs,
            "scroll_to_load_more_jobs",
            lambda: _false(),
        )

        await write_response.send_job_descriptions_to_chat(
            usr_name="测试",
            url="https://example.test",
            browser_type="chrome",
            label="后端开发",
            dry_run=True,
        )

        # 首次一次 + reload 后补选一次
        assert selected == ["后端开发", "后端开发"]

    asyncio.run(scenario())


async def _false() -> bool:
    return False


async def _metadata(index: int) -> dict:
    return {
        "company_name": f"公司{index}",
        "job_title": f"岗位{index}",
        "location": "成都",
        "job_url": f"https://example.test/job/{index}",
    }


def test_virtual_job_list_uses_visible_card_index_after_scroll(monkeypatch):
    async def scenario():
        patch = _patch_common_browser(monkeypatch)
        requested: list[int] = []
        scrolls: list[bool] = []

        async def get_jd(index: int):
            requested.append(index)
            if index <= 15:
                return f"可见岗位{index} JD Go Redis 后端开发"
            return None

        async def get_text(selector: str, timeout: float = 5):
            return "继续沟通"

        async def loaded_count():
            return 15

        async def scroll_more():
            scrolls.append(True)
            return len(scrolls) == 1

        monkeypatch.setattr(
            write_response.finding_jobs, "get_job_description_by_index", get_jd
        )
        monkeypatch.setattr(write_response.finding_jobs, "get_text_by_css", get_text)
        monkeypatch.setattr(
            write_response.finding_jobs, "get_loaded_job_count", loaded_count
        )
        monkeypatch.setattr(
            write_response.finding_jobs, "scroll_to_load_more_jobs", scroll_more
        )

        await write_response.send_job_descriptions_to_chat(
            usr_name="测试",
            url="https://example.test",
            browser_type="chrome",
            label="",
            dry_run=True,
        )

        assert requested[:17] == [*range(1, 16), 16, 15]
        assert any(
            kind == "job_found" and payload["index"] == 16
            for kind, payload in patch.events
        )

    asyncio.run(scenario())


def test_send_response_requires_return_to_job_list(monkeypatch):
    async def scenario():
        clicked: list[bool] = []

        async def click_and_confirm(timeout: float = 20):
            clicked.append(True)
            return {"ok": True, "greeting": "BOSS 实际招呼语"}

        async def dismiss():
            return False

        async def fake_sleep(delay: float):
            return None

        monkeypatch.setattr(
            write_response.finding_jobs, "click_contact_and_confirm", click_and_confirm
        )
        monkeypatch.setattr(
            write_response.finding_jobs, "dismiss_contact_confirmation", dismiss
        )
        monkeypatch.setattr(write_response.asyncio, "sleep", fake_sleep)

        with pytest.raises(RuntimeError, match="未能关闭发送结果层"):
            await write_response.send_response_and_go_back("hello")
        assert clicked == [True]

    asyncio.run(scenario())


def test_sent_greeting_is_logged_even_when_return_to_list_fails(monkeypatch):
    """招呼语已发出、返回列表失败：仍必须记 sent=True 并干净收尾，不能吞掉这条发送。"""

    async def scenario():
        patch = _patch_common_browser(monkeypatch)
        logged: list[dict] = []

        async def get_jd(index: int):
            return f"岗位{index} JD Go Redis 后端开发"

        async def get_text(selector: str, timeout: float = 5):
            return "立即沟通"

        async def click_and_confirm(timeout: float = 20):
            return {"ok": True, "greeting": "BOSS 实际招呼语"}

        async def dismiss():
            return False

        monkeypatch.setattr(
            write_response.finding_jobs, "get_job_description_by_index", get_jd
        )
        monkeypatch.setattr(write_response.finding_jobs, "get_text_by_css", get_text)
        monkeypatch.setattr(
            write_response.finding_jobs, "click_contact_and_confirm", click_and_confirm
        )
        monkeypatch.setattr(
            write_response.finding_jobs, "dismiss_contact_confirmation", dismiss
        )
        monkeypatch.setattr(
            write_response, "log_attempt", lambda **kwargs: logged.append(kwargs)
        )

        await write_response.send_job_descriptions_to_chat(
            usr_name="测试",
            url="https://example.test",
            browser_type="chrome",
            label="",
            dry_run=False,
        )

        # 消息发出去了 → 恰好一条 sent=True，然后 break 收尾（而不是异常冒泡吞掉记录）。
        sent_records = [kw for kw in logged if kw.get("sent")]
        assert len(sent_records) == 1
        assert any(
            kind == "letter_sent" and payload.get("status") == "sent"
            for kind, payload in patch.events
        )
        assert not any(kind == "error" for kind, _ in patch.events)

    asyncio.run(scenario())


def test_send_limit_stops_after_configured_successful_sends(monkeypatch):
    async def scenario():
        patch = _patch_common_browser(monkeypatch)
        requested: list[int] = []
        confirmed: list[int] = []
        dismissed: list[int] = []
        logged: list[dict] = []
        csv_rows: list[dict] = []

        async def get_jd(index: int):
            requested.append(index)
            return f"岗位{index} JD Go Redis 后端开发"

        async def get_text(selector: str, timeout: float = 5):
            return "立即沟通"

        async def click_and_confirm(timeout: float = 20):
            confirmed.append(len(confirmed) + 1)
            return {"ok": True, "greeting": f"BOSS 实际招呼语{len(confirmed)}"}

        async def dismiss():
            dismissed.append(len(dismissed) + 1)
            return True

        monkeypatch.setenv("BOSS_AUTO_SEND_MAX_SENT", "2")
        monkeypatch.setattr(
            write_response.finding_jobs, "get_job_description_by_index", get_jd
        )
        monkeypatch.setattr(write_response.finding_jobs, "get_text_by_css", get_text)
        monkeypatch.setattr(
            write_response.finding_jobs, "click_contact_and_confirm", click_and_confirm
        )
        monkeypatch.setattr(
            write_response.finding_jobs, "dismiss_contact_confirmation", dismiss
        )
        monkeypatch.setattr(
            write_response, "log_attempt", lambda **kwargs: logged.append(kwargs)
        )
        monkeypatch.setattr(
            write_response,
            "log_sent_application",
            lambda **kwargs: csv_rows.append(kwargs) or True,
        )

        await write_response.send_job_descriptions_to_chat(
            usr_name="测试",
            url="https://example.test",
            browser_type="chrome",
            label="",
            dry_run=False,
        )

        # 两次都完整走过“点击即发送 -> 确认 -> 留在此页”，并推进到下一张岗位卡。
        assert confirmed == [1, 2]
        assert dismissed == [1, 2]
        assert requested == [1, 2]
        assert [row["letter"] for row in logged if row.get("sent")] == [
            "BOSS 实际招呼语1",
            "BOSS 实际招呼语2",
        ]
        assert [row["job_metadata"]["company_name"] for row in csv_rows] == [
            "公司1",
            "公司2",
        ]
        assert [row["job_description"] for row in csv_rows] == [
            "岗位1 JD Go Redis 后端开发",
            "岗位2 JD Go Redis 后端开发",
        ]
        assert any(
            kind == "feed_exhausted" and payload["total"] == 2
            for kind, payload in patch.events
        )

    asyncio.run(scenario())


def test_login_timeout_stops_before_selecting_expectation(monkeypatch):
    async def scenario():
        patch = _patch_common_browser(monkeypatch)
        selected: list[str] = []

        async def login_failed():
            return False

        async def select_label(label: str):
            selected.append(label)
            return True

        monkeypatch.setattr(write_response.finding_jobs, "log_in", login_failed)
        monkeypatch.setattr(
            write_response.finding_jobs, "select_dropdown_option", select_label
        )

        await write_response.send_job_descriptions_to_chat(
            usr_name="测试",
            url="https://example.test",
            browser_type="chrome",
            label="Python",
            dry_run=True,
        )

        assert selected == []
        errors = [payload for kind, payload in patch.events if kind == "error"]
        assert errors and errors[0]["stage"] == "login_not_confirmed"
        assert "login_ok" not in [kind for kind, _ in patch.events]

    asyncio.run(scenario())


def test_explicit_send_limit_overrides_environment_value(monkeypatch):
    """GUI 传进来的上限必须优先于历史环境变量，防止前端默认值/加载时序污染。"""

    async def scenario():
        patch = _patch_common_browser(monkeypatch)
        sent: list[str] = []

        async def get_jd(index: int):
            return f"岗位{index} JD Python AI 应用开发"

        async def get_text(selector: str, timeout: float = 5):
            return "立即沟通"

        async def send_response(response: str):
            sent.append(response)
            return response

        monkeypatch.setenv("BOSS_AUTO_SEND_MAX_SENT", "10")
        monkeypatch.setattr(write_response.finding_jobs, "get_job_description_by_index", get_jd)
        monkeypatch.setattr(write_response.finding_jobs, "get_text_by_css", get_text)
        monkeypatch.setattr(write_response, "send_response_and_go_back", send_response)

        await write_response.send_job_descriptions_to_chat(
            usr_name="测试",
            url="https://example.test",
            browser_type="chrome",
            label="",
            dry_run=False,
            max_successful_sends=2,
        )

        assert len(sent) == 2
        config = next(payload for kind, payload in patch.events if kind == "run_config")
        assert config["max_successful_sends"] == 2
        finish = next(payload for kind, payload in patch.events if kind == "feed_exhausted")
        assert finish["reason"] == "达到成功投递上限"
        assert finish["sent_count"] == 2

    asyncio.run(scenario())


def test_dry_run_progress_explicitly_says_not_applied(monkeypatch):
    """模拟运行不能只显示 letter_sent，必须明确写出没有投递。"""

    async def scenario():
        patch = _patch_common_browser(monkeypatch)

        async def get_jd(index: int):
            return "岗位 JD Python AI 应用开发" if index == 1 else None

        async def get_text(selector: str, timeout: float = 5):
            return "立即沟通"

        async def no_scroll():
            return False

        monkeypatch.setattr(
            write_response.finding_jobs, "get_job_description_by_index", get_jd
        )
        monkeypatch.setattr(write_response.finding_jobs, "get_text_by_css", get_text)
        monkeypatch.setattr(
            write_response.finding_jobs, "scroll_to_load_more_jobs", no_scroll
        )

        await write_response.send_job_descriptions_to_chat(
            usr_name="测试",
            url="https://example.test",
            browser_type="chrome",
            label="",
            dry_run=True,
        )

        outcome = next(
            payload for kind, payload in patch.events
            if kind == "letter_sent" and payload["status"] == "dry_run"
        )
        assert outcome["application_status"] == "未投递"
        assert outcome["application_reason"] == "模拟运行（Dry Run）不发送"

    asyncio.run(scenario())


def test_successful_send_waits_random_delay_between_jobs(monkeypatch):
    async def scenario():
        patch = _patch_common_browser(monkeypatch)
        sent: list[str] = []

        async def get_jd(index: int):
            if index <= 2:
                return f"岗位{index} JD Go Redis 后端开发"
            return None

        async def get_text(selector: str, timeout: float = 5):
            return "立即沟通"

        async def click(xpath: str, timeout: float = 10):
            return True

        async def wait(selector: str, timeout: float = 50):
            return True

        async def send_response(response: str):
            sent.append(response)
            return response

        async def no_scroll():
            return False

        monkeypatch.setenv("BOSS_AUTO_SEND_MAX_SENT", "3")
        monkeypatch.setenv("BOSS_AUTO_SEND_DELAY_MIN", "10")
        monkeypatch.setenv("BOSS_AUTO_SEND_DELAY_MAX", "60")
        monkeypatch.setattr(write_response.random, "uniform", lambda low, high: 23.5)
        monkeypatch.setattr(
            write_response.finding_jobs, "get_job_description_by_index", get_jd
        )
        monkeypatch.setattr(write_response.finding_jobs, "get_text_by_css", get_text)
        monkeypatch.setattr(write_response.finding_jobs, "click_by_xpath", click)
        monkeypatch.setattr(write_response.finding_jobs, "wait_for_css", wait)
        monkeypatch.setattr(
            write_response.finding_jobs, "scroll_to_load_more_jobs", no_scroll
        )
        monkeypatch.setattr(write_response, "send_response_and_go_back", send_response)

        await write_response.send_job_descriptions_to_chat(
            usr_name="测试",
            url="https://example.test",
            browser_type="chrome",
            label="",
            dry_run=False,
        )

        # 本工具不生成招呼语；空串只是弹窗无法提取文案时的审计兜底参数。
        assert sent == ["", ""]
        assert 23.5 in patch.sleeps

    asyncio.run(scenario())
