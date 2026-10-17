from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def safe_name(value: str, fallback: str = "untitled", limit: int = 100) -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .-")
    return (value or fallback)[:limit].rstrip(" .")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    body = "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values)
    atomic_write(path, body.encode("utf-8"))


def utc_now() -> str:
    return datetime.now(UTC).isoformat()

