"""YAML + .env 기반 설정 로더."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

try:  # python-dotenv 는 선택 의존성처럼 동작시킨다
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "config.yaml"
LOCAL_CONFIG = REPO_ROOT / "config" / "local.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    """override 를 base 위에 재귀적으로 덮어쓴다."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    """점 표기법으로 읽는 얇은 설정 래퍼."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def get(self, path: str, default: Any = None) -> Any:
        """`platforms.youtube.daily_max` 형태로 조회."""
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node if node is not None else default

    def require(self, path: str) -> Any:
        value = self.get(path)
        if value is None:
            raise KeyError(f"설정값이 없습니다: {path}")
        return value

    def section(self, path: str) -> dict[str, Any]:
        value = self.get(path, {})
        return value if isinstance(value, dict) else {}

    def path(self, path: str, default: str) -> Path:
        """레포 루트 기준 경로로 해석."""
        raw = str(self.get(path, default))
        candidate = Path(raw)
        return candidate if candidate.is_absolute() else REPO_ROOT / candidate

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def __repr__(self) -> str:  # pragma: no cover
        return f"Config(keys={list(self._data)})"


def load_config(config_path: str | Path | None = None) -> Config:
    """config.yaml → local.yaml 순으로 병합하고 .env 를 읽어들인다."""
    load_dotenv(REPO_ROOT / ".env")

    base_path = Path(config_path) if config_path else DEFAULT_CONFIG
    with open(base_path, encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}

    if LOCAL_CONFIG.exists():
        with open(LOCAL_CONFIG, encoding="utf-8") as fp:
            data = _deep_merge(data, yaml.safe_load(fp) or {})

    # CI 등에서 드라이런 강제
    if os.getenv("AUTOPUB_DRY_RUN", "").lower() in ("1", "true", "yes"):
        data.setdefault("general", {})["dry_run"] = True

    return Config(data)


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    """환경변수 조회. required 면 없을 때 예외."""
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(
            f"환경변수 {name} 가 필요합니다. .env 또는 GitHub Secrets 를 확인하세요."
        )
    return value or ""
