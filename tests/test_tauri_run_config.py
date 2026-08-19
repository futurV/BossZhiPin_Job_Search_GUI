"""GUI 当前表单必须覆盖旧环境变量，且在后台任务启动前完成。"""
import os

from boss_zhipin.tauri import RunConfig, _apply_run_config


def test_current_gui_values_override_stale_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("BOSS_LABEL", "旧标签")
    monkeypatch.setenv("BOSS_AUTO_SEND_MAX_SENT", "10")

    config = RunConfig(
        usrName="当前用户",
        label="",
        dryRun=False,
        maxSent=50,
        delayMin=3,
        delayMax=7,
    )
    _apply_run_config(config)

    assert "DRY_RUN" not in os.environ
    assert "BOSS_LABEL" not in os.environ
    assert os.environ["BOSS_USR_NAME"] == "当前用户"
    assert os.environ["BOSS_AUTO_SEND_MAX_SENT"] == "50"
    assert os.environ["BOSS_AUTO_SEND_DELAY_MIN"] == "3.0"
    assert os.environ["BOSS_AUTO_SEND_DELAY_MAX"] == "7.0"

    saved = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "BOSS_USR_NAME='当前用户'" in saved or "BOSS_USR_NAME=当前用户" in saved
    assert "BOSS_AUTO_SEND_MAX_SENT=50" in saved
    assert "BOSS_LABEL" not in saved
