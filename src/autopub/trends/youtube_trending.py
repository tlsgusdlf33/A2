"""YouTube 인기 급상승 영상 기반 주제 수집.

videos.list(chart=mostPopular) 는 1 유닛만 소모하므로
업로드 쿼터(1600 유닛/건)에 사실상 영향을 주지 않는다.
API 키가 없으면 조용히 건너뛴다.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from ..http import get_json
from ..logutil import get_logger
from .models import Evidence, Topic

log = get_logger(__name__)

API_URL = "https://www.googleapis.com/youtube/v3/videos"
_BRACKETS = re.compile(r"[\[\(【][^\]\)】]{0,40}[\]\)】]")


def _clean_title(title: str) -> str:
    """'[속보]', '(풀영상)' 같은 장식과 이모지 잡음을 제거."""
    cleaned = _BRACKETS.sub(" ", title)
    cleaned = re.sub(r"[|#]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch(region_code: str = "KR", max_results: int = 30, weight: float = 0.8) -> list[Topic]:
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        log.info("YOUTUBE_API_KEY 미설정 — YouTube 트렌드 수집을 건너뜁니다")
        return []

    params = {
        "part": "snippet,statistics",
        "chart": "mostPopular",
        "regionCode": region_code,
        "maxResults": str(min(max_results, 50)),
        "key": api_key,
    }

    try:
        payload = get_json(API_URL, params=params)
    except Exception as exc:
        log.warning("YouTube 트렌드 수집 실패: %s", exc)
        return []

    items = payload.get("items", [])
    topics: list[Topic] = []

    for rank, item in enumerate(items):
        snippet = item.get("snippet", {})
        title = _clean_title(snippet.get("title", ""))
        if not title:
            continue

        stats = item.get("statistics", {})
        views = int(stats.get("viewCount", 0) or 0)
        rank_score = max(0.2, 1.0 - rank / max(len(items), 1))

        topics.append(
            Topic(
                title=title,
                score=rank_score * weight,
                sources=["youtube_trending"],
                evidence=[
                    Evidence(
                        title=snippet.get("title", ""),
                        url=f"https://www.youtube.com/watch?v={item.get('id', '')}",
                        source=snippet.get("channelTitle", ""),
                        snippet=(snippet.get("description", "") or "")[:400],
                        published_at=_parse_iso(snippet.get("publishedAt")),
                    )
                ],
                traffic=f"{views:,} views",
                raw={"video_id": item.get("id"), "views": views},
            )
        )

    log.info("YouTube 트렌드(%s): %d개 주제 수집", region_code, len(topics))
    return topics
