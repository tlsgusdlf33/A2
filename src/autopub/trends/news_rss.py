"""뉴스 RSS 기반 주제 수집 (Google News 등, 무료)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..http import get_text
from ..logutil import get_logger
from ..util import now_kst
from .models import Evidence, Topic

log = get_logger(__name__)


def _entry_datetime(entry) -> datetime | None:
    import time as _time

    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return None
    return datetime.fromtimestamp(_time.mktime(parsed), tz=timezone.utc)


# 각종 대시/파이프 + 짧은 언론사명으로 끝나는 꼬리표
_SOURCE_TAIL = re.compile(r"\s*[-\u2010-\u2015\u2212|]\s*[^\s\-|]{1,20}\s*$")


def _clean_title(title: str) -> str:
    """Google News 제목의 ' - 언론사' 꼬리표를 떼어낸다."""
    cleaned = title.replace("\u00a0", " ").strip()
    stripped = _SOURCE_TAIL.sub("", cleaned).strip()
    # 꼬리를 떼고 나서 너무 짧아지면 원본을 유지(본문 자체가 짧은 경우)
    return stripped if len(stripped) >= 8 else cleaned


def fetch(feeds: list[str], weight: float = 0.6, max_age_hours: int = 24) -> list[Topic]:
    try:
        import feedparser
    except ImportError:  # pragma: no cover
        log.warning("feedparser 미설치 — 뉴스 RSS 수집을 건너뜁니다")
        return []

    cutoff = now_kst() - timedelta(hours=max_age_hours)
    topics: list[Topic] = []

    for feed_url in feeds:
        try:
            raw = get_text(feed_url, timeout=15.0)
        except Exception as exc:
            log.warning("RSS 수집 실패 (%s): %s", feed_url, exc)
            continue

        parsed = feedparser.parse(raw)
        entries = parsed.entries[:25]
        for rank, entry in enumerate(entries):
            title = _clean_title(getattr(entry, "title", "") or "")
            if not title:
                continue

            published = _entry_datetime(entry)
            if published and published < cutoff:
                continue

            source = ""
            if getattr(entry, "source", None) is not None:
                source = getattr(entry.source, "title", "") or ""

            rank_score = max(0.2, 1.0 - rank / max(len(entries), 1))
            topics.append(
                Topic(
                    title=title,
                    score=rank_score * weight,
                    sources=["news_rss"],
                    evidence=[
                        Evidence(
                            title=title,
                            url=getattr(entry, "link", "") or "",
                            source=source,
                            snippet=(getattr(entry, "summary", "") or "")[:400],
                            published_at=published,
                        )
                    ],
                )
            )

    log.info("뉴스 RSS: %d개 주제 수집 (피드 %d개)", len(topics), len(feeds))
    return topics
