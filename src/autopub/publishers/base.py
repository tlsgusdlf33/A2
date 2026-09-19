"""발행자 공통 인터페이스 + 일일 한도/간격 게이트."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..logutil import get_logger
from ..state import State

log = get_logger(__name__)


class PublishError(RuntimeError):
    """발행 실패."""


class QuotaExceeded(PublishError):
    """오늘 할당량 소진."""


@dataclass
class PublishResult:
    platform: str
    title: str
    url: str = ""
    external_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class Publisher(abc.ABC):
    platform = "base"

    def __init__(self, config: Config, state: State):
        self.config = config
        self.state = state
        self.settings = config.section(f"platforms.{self.platform}")

    # ---------------- 한도 ----------------

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", True))

    @property
    def daily_max(self) -> int:
        """하루 최대 발행 수. 플랫폼별로 재정의할 수 있다."""
        return int(self.settings.get("daily_max") or 0)

    @property
    def min_gap_minutes(self) -> float:
        return float(self.settings.get("min_gap_minutes", 0))

    def remaining_today(self) -> int:
        return max(0, self.daily_max - self.state.count_today(self.platform))

    def check_ready(self) -> tuple[bool, str]:
        """지금 발행해도 되는지. (가능여부, 사유)"""
        if not self.enabled:
            return False, "설정에서 비활성화됨"

        used = self.state.count_today(self.platform)
        if used >= self.daily_max:
            return False, f"오늘 할당량 소진 ({used}/{self.daily_max})"

        gap = self.state.minutes_since_last(self.platform)
        if gap < self.min_gap_minutes:
            wait = self.min_gap_minutes - gap
            return False, f"최소 간격 미충족 (앞으로 {wait:.0f}분 더 필요)"

        return True, f"발행 가능 ({used}/{self.daily_max} 사용)"

    # ---------------- 구현 ----------------

    @abc.abstractmethod
    def publish(self, payload: Any) -> PublishResult:
        """실제 발행. 실패 시 PublishError 를 던진다."""

    def close(self) -> None:
        """리소스 정리(브라우저 등). 필요한 쪽에서 재정의."""
