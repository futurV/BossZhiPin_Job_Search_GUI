"""GUI 当前表单与 Tauri Channel 的关键回归。"""
import json
import os

from boss_zhipin.tauri import RunConfig, _apply_run_config, _safe_send


def test_current_gui_values_override_stale_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("BOSS_LABEL", "旧标签")
    monkeypatch.setenv("BOSS_AUTO_SEND_MAX_SENT", "10")

    config = RunConfig(
        label="",
        dryRun=False,
        maxSent=50,
        delayMin=3,
        delayMax=7,
    )
    _apply_run_config(config)

    assert "DRY_RUN" not in os.environ
    assert "BOSS_LABEL" not in os.environ
    assert os.environ["BOSS_AUTO_SEND_MAX_SENT"] == "50"
    assert os.environ["BOSS_AUTO_SEND_DELAY_MIN"] == "3.0"
    assert os.environ["BOSS_AUTO_SEND_DELAY_MAX"] == "7.0"

    saved = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "BOSS_AUTO_SEND_MAX_SENT=50" in saved
    assert "BOSS_LABEL" not in saved


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
