from __future__ import annotations

import re


TIMING = re.compile(r"(?P<start>\d{2}:\d{2}:\d{2}[.,]\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2}[.,]\d{3})")
TAG = re.compile(r"<[^>]+>")


def to_timestamped_text(vtt: str) -> str:
    lines = vtt.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    index = 0
    previous = ""
    while index < len(lines):
        match = TIMING.search(lines[index])
        if not match:
            index += 1
            continue
        start = match.group("start").replace(",", ".")
        index += 1
        cue: list[str] = []
        while index < len(lines) and lines[index].strip():
            cleaned = TAG.sub("", lines[index]).strip()
            if cleaned:
                cue.append(cleaned)
            index += 1
        text = " ".join(cue)
        if text and text != previous:
            output.append(f"[{start}] {text}")
            previous = text
    return "\n".join(output) + ("\n" if output else "")

