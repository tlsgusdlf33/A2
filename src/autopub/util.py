"""공용 유틸리티."""
from __future__ import annotations

import hashlib
import re
import subprocess
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def today_key(dt: datetime | None = None) -> str:
    """KST 기준 YYYY-MM-DD."""
    return (dt or now_kst()).astimezone(KST).strftime("%Y-%m-%d")


def topic_key(text: str) -> str:
    """주제 중복 판정용 안정 키. 공백/기호/대소문자 무시."""
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = re.sub(r"[^0-9a-z가-힣]+", "", normalized)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def slugify_ko(text: str, max_len: int = 60) -> str:
    """파일명용 슬러그. 한글은 유지한다."""
    normalized = unicodedata.normalize("NFKC", text).strip()
    normalized = re.sub(r"[^\w가-힣\s-]", "", normalized)
    normalized = re.sub(r"[\s_-]+", "-", normalized).strip("-")
    return (normalized[:max_len] or "untitled").lower()


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    """플랫폼 글자수 제한에 맞춰 자른다."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def run(cmd: list[str], *, timeout: int = 900) -> subprocess.CompletedProcess:
    """외부 명령(주로 ffmpeg) 실행. 실패 시 stderr 를 포함해 예외."""
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-2000:]
        raise RuntimeError(
            f"명령 실패 (exit {proc.returncode}): {' '.join(cmd[:6])} …\n{tail}"
        )
    return proc


def have_binary(name: str) -> bool:
    from shutil import which

    return which(name) is not None
