"""트렌드 도메인 모델."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..util import topic_key


@dataclass
class Evidence:
    """주제를 뒷받침하는 실제 기사/영상 — LLM 환각 방지용 근거."""

    title: str
    url: str = ""
    source: str = ""
    snippet: str = ""
    published_at: datetime | None = None

    def as_prompt_line(self) -> str:
        bits = [f"- {self.title}"]
        if self.source:
            bits.append(f"({self.source})")
        if self.snippet:
            bits.append(f": {self.snippet[:200]}")
        return " ".join(bits)


@dataclass
class Topic:
    """발행 후보 주제."""

    title: str
    score: float = 0.0
    sources: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    traffic: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return topic_key(self.title)

    @property
    def freshest(self) -> datetime | None:
        stamps = [e.published_at for e in self.evidence if e.published_at]
        return max(stamps) if stamps else None

    def merge(self, other: "Topic") -> None:
        """같은 주제를 여러 소스에서 찾았을 때 합친다."""
        self.score += other.score
        for source in other.sources:
            if source not in self.sources:
                self.sources.append(source)
        seen = {(e.title, e.url) for e in self.evidence}
        for item in other.evidence:
            if (item.title, item.url) not in seen:
                self.evidence.append(item)
                seen.add((item.title, item.url))
        if not self.traffic and other.traffic:
            self.traffic = other.traffic

    def evidence_block(self, limit: int = 8) -> str:
        return "\n".join(e.as_prompt_line() for e in self.evidence[:limit])

    def __repr__(self) -> str:  # pragma: no cover
        return f"Topic({self.title!r}, score={self.score:.2f}, sources={self.sources})"
