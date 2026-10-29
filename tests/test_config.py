from pathlib import Path

from lms_collector.config import Settings


def test_loads_yaml_behavior_and_env_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text("LMS_USERNAME=student\nGOOGLE_CHAT_WEBHOOK_URL=https://secret.example\n", encoding="utf-8")
    yaml = tmp_path / "config.yml"
    yaml.write_text(
        "lms:\n  base_url: https://moodle.example.edu\n"
        "storage:\n  internal_dir: data\n  resources_dir: ../Resources\n"
        "browser:\n  headless: true\n"
        "deadlines:\n  reminder_hours: [24, 3, 72]\n  digest_windows_days: {urgent: 2, upcoming: 7}\n"
        "  recent_missed_days: 7\n",
        encoding="utf-8",
    )
    config = Settings.load(env, yaml)
    assert config.output_dir == (tmp_path / "data").resolve()
    assert config.resources_dir == (tmp_path / "../Resources").resolve()
    assert config.reminder_hours == (72, 24, 3)
    assert config.headless is True
    assert config.username == "student"
    assert config.google_chat_webhook_url == "https://secret.example"
    assert config.google_chat_enabled is False
    assert config.recent_missed_days == 7
