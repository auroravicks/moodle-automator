from pathlib import Path

import pytest

from lms_collector.config import Settings


@pytest.fixture
def config(tmp_path: Path, monkeypatch) -> Settings:
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text("LMS_USERNAME=user\nGOOGLE_CHAT_WEBHOOK_URL=https://secret.example\n", encoding="utf-8")
    yaml = tmp_path / "config.yml"
    yaml.write_text("lms:\n  base_url: https://moodle.example.edu\nstorage:\n  internal_dir: data\n", encoding="utf-8")
    return Settings.load(env, yaml)
