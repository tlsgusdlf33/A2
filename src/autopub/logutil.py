"""표준 로깅 설정."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_CONFIGURED = False


def setup_logging(level: str | None = None, log_dir: str | Path = "logs") -> None:
    """콘솔 + 파일 로깅을 1회만 설정한다."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    root = logging.getLogger()
    root.setLevel(getattr(logging, level_name, logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    try:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path / "autopub.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        # 읽기 전용 파일시스템(일부 CI)에서는 콘솔만 사용
        pass

    # 서드파티 소음 억제
    for noisy in ("httpx", "httpcore", "googleapiclient.discovery_cache", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
