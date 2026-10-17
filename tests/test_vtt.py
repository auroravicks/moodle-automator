from lms_collector.vtt import to_timestamped_text


def test_converts_vtt_to_timestamped_text() -> None:
    source = """WEBVTT

00:00:01.000 --> 00:00:03.000
Hello <i>class</i>.

00:00:04.000 --> 00:00:05.000
Next point.
"""
    assert to_timestamped_text(source) == "[00:00:01.000] Hello class.\n[00:00:04.000] Next point.\n"
