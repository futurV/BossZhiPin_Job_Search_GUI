"""GUI 当前表单与 Tauri Channel 的关键回归。"""
import asyncio
import json
import os

from boss_zhipin.tauri import (
    RunConfig,
    _apply_run_config,
    _build_main_loop_factory,
    _safe_send,
)


def test_current_gui_values_override_stale_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("BOSS_LABEL", "旧标签")
    monkeypatch.setenv("BOSS_AUTO_SEND_MAX_SENT", "10")
    monkeypatch.setenv("BOSS_MIN_MATCH_SCORE", "50")
    monkeypatch.setenv("BOSS_MIN_SALARY_K", "0")

    config = RunConfig(
        label="",
        dryRun=False,
        minMatchScore=82,
        minSalaryK=18,
        maxSent=50,
        delayMin=3,
        delayMax=7,
    )
    _apply_run_config(config)

    assert "DRY_RUN" not in os.environ
    assert "BOSS_LABEL" not in os.environ
    assert os.environ["BOSS_AUTO_SEND_MAX_SENT"] == "50"
    assert os.environ["BOSS_MIN_MATCH_SCORE"] == "82"
    assert os.environ["BOSS_MIN_SALARY_K"] == "18"
    assert os.environ["BOSS_AUTO_SEND_DELAY_MIN"] == "3.0"
    assert os.environ["BOSS_AUTO_SEND_DELAY_MAX"] == "7.0"

    saved = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "BOSS_AUTO_SEND_MAX_SENT=50" in saved
    assert "BOSS_MIN_MATCH_SCORE=82" in saved
    assert "BOSS_MIN_SALARY_K=18" in saved
    assert "BOSS_LABEL" not in saved


def test_run_config_default_match_score_is_70():
    assert RunConfig().min_match_score == 70
    assert RunConfig().min_salary_k == 0


def test_run_match_score_is_passed_directly_to_provider(monkeypatch):
    from boss_zhipin import cli

    captured: dict = {}

    async def fake_run_provider(**kwargs):
        captured.update(kwargs)

    monkeypatch.delenv("RESUME_PATH", raising=False)
    monkeypatch.setattr(cli, "run_provider", fake_run_provider)

    config = RunConfig(minMatchScore=86, minSalaryK=20)
    asyncio.run(_build_main_loop_factory(config)())

    assert captured["min_llm_score"] == 86
    assert captured["min_salary_k"] == 20


def test_log_channel_sends_a_json_string():
    sent: list[str] = []

    class FakeChannel:
        def send(self, data: str) -> None:
            # PyTauri 会把 str 当 JSON 文本解析；无效 JSON 在真实 Channel 中发送失败。
            json.loads(data)
            sent.append(data)

    message = "16:39:20 [INFO] 测试日志"
    _safe_send(FakeChannel(), message)

    assert len(sent) == 1
    assert json.loads(sent[0]) == message
