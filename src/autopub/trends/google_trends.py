"""Google Trends 실시간 인기 검색어 (무료 RSS, API 키 불필요)."""
from __future__ import annotations

from datetime import datetime
from xml.etree import ElementTree

from ..http import get_text
from ..logutil import get_logger
from .models import Evidence, Topic

log = get_logger(__name__)

NS = {"ht": "https://trends.google.com/trending/rss"}


def _parse_pubdate(value: str | None) -> datetime | None:
    if not value:
        return None
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def fetch(geo: str = "KR", rss_url: str | None = None, weight: float = 1.0) -> list[Topic]:
    """실시간 급상승 검색어를 순위 가중치와 함께 반환."""
    url = (rss_url or "https://trends.google.com/trending/rss?geo={geo}").format(geo=geo)
    try:
        xml = get_text(url)
    except Exception as exc:  # 네트워크/차단 등은 치명적이지 않게
        log.warning("Google Trends 수집 실패: %s", exc)
        return []

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        log.warning("Google Trends RSS 파싱 실패: %s", exc)
        return []

    items = root.findall(".//item")
    topics: list[Topic] = []

    for rank, item in enumerate(items):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue

        # 상위일수록 높은 점수 (1.0 → 0.2 선형 감쇠)
        rank_score = max(0.2, 1.0 - rank / max(len(items), 1))
        traffic = (item.findtext("ht:approx_traffic", namespaces=NS) or "").strip()

        evidence = []
        for news in item.findall("ht:news_item", NS):
            evidence.append(
                Evidence(
                    title=(news.findtext("ht:news_item_title", namespaces=NS) or "").strip(),
                    url=(news.findtext("ht:news_item_url", namespaces=NS) or "").strip(),
                    source=(news.findtext("ht:news_item_source", namespaces=NS) or "").strip(),
                    snippet=(news.findtext("ht:news_item_snippet", namespaces=NS) or "").strip(),
                    published_at=_parse_pubdate(item.findtext("pubDate")),
                )
            )

        topics.append(
            Topic(
                title=title,
                score=rank_score * weight,
                sources=["google_trends"],
                evidence=evidence,
                traffic=traffic,
                raw={"rank": rank, "approx_traffic": traffic},
            )
        )

    log.info("Google Trends(%s): %d개 주제 수집", geo, len(topics))
    return topics
