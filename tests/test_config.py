from pathlib import Path

from lms_collector.config import Settings


def test_loads_plain_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text(
        "LMS_BASE_URL=https://moodle.example.edu\nOUTPUT_DIR=data\nREMINDER_HOURS=24,3,72\nHEADLESS=true\n",
        encoding="utf-8",
    )
    config = Settings.load(env)
    assert config.output_dir == (tmp_path / "data").resolve()
    assert config.resources_dir == (tmp_path / "../Resources").resolve()
    assert config.reminder_hours == (72, 24, 3)
    assert config.headless is True
