"""여러 트렌드 소스를 합쳐 발행 후보 주제를 뽑는다."""
from __future__ import annotations

import difflib
import re
from datetime import timedelta

from ..config import Config
from ..logutil import get_logger
from ..state import State
from ..util import now_kst
from . import google_trends, news_rss, youtube_trending
from .models import Topic

log = get_logger(__name__)

# 두 제목이 이만큼 닮으면 같은 주제로 본다
SIMILARITY_THRESHOLD = 0.72

HANGUL = re.compile(r"[가-힣]")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _same_topic(a: str, b: str) -> bool:
    """두 제목이 같은 사안을 가리키는지 판정.

    Google Trends 는 "최예나" 같은 키워드를, 뉴스 RSS 는 그 키워드가 들어간
    긴 헤드라인을 준다. 문자열 유사도만으로는 절대 합쳐지지 않으므로
    "짧은 쪽이 긴 쪽에 포함되는가"를 함께 본다.
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    # 2글자 이상인 키워드가 통째로 들어있으면 같은 사안으로 본다
    if len(short) >= 2 and short in long_:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= SIMILARITY_THRESHOLD


class TrendAggregator:
    def __init__(self, config: Config, state: State | None = None):
        self.config = config
        self.state = state

    # ---------------- 수집 ----------------

    def collect(self) -> list[Topic]:
        cfg = self.config
        geo = cfg.get("trends.geo", "KR")
        collected: list[Topic] = []

        gt = cfg.section("trends.sources.google_trends")
        if gt.get("enabled", True):
            collected += google_trends.fetch(
                geo=geo,
                rss_url=gt.get("rss_url"),
                weight=float(gt.get("weight", 1.0)),
            )

        yt = cfg.section("trends.sources.youtube_trending")
        if yt.get("enabled", True):
            collected += youtube_trending.fetch(
                region_code=yt.get("region_code", geo),
                max_results=int(yt.get("max_results", 30)),
                weight=float(yt.get("weight", 0.8)),
            )

        nr = cfg.section("trends.sources.news_rss")
        if nr.get("enabled", True):
            collected += news_rss.fetch(
                feeds=list(nr.get("feeds", [])),
                weight=float(nr.get("weight", 0.6)),
                max_age_hours=int(cfg.get("trends.max_age_hours", 24)),
            )

        return collected

    # ---------------- 정제 ----------------

    def _merge_similar(self, topics: list[Topic]) -> list[Topic]:
        """제목이 유사한 주제를 하나로 합친다.

        여러 소스에 동시에 등장한 주제일수록 점수가 누적되어 자연스럽게 상위로 올라간다.
        """
        merged: list[Topic] = []
        for topic in sorted(topics, key=lambda t: t.score, reverse=True):
            for existing in merged:
                if existing.key == topic.key or _same_topic(existing.title, topic.title):
                    existing.merge(topic)
                    break
            else:
                merged.append(topic)
        return merged

    def _passes_filters(self, topic: Topic) -> bool:
        blocked = [k for k in self.config.get("trends.blocklist_keywords", []) if k]
        haystack = topic.title + " " + " ".join(e.title for e in topic.evidence)
        for keyword in blocked:
            if keyword in haystack:
                log.debug("차단 키워드로 제외: %s (%s)", topic.title, keyword)
                return False

        # 짧은 영문 약어("pl", "afc")는 모호해서 글감이 되지 않는다
        has_hangul = bool(HANGUL.search(topic.title))
        if len(topic.title.strip()) < (2 if has_hangul else 5):
            log.debug("제목이 너무 짧아 제외: %s", topic.title)
            return False

        # 근거가 하나도 없으면 LLM 이 환각할 위험이 커서 제외
        if not topic.evidence:
            log.debug("근거 기사 없음으로 제외: %s", topic.title)
            return False

        # 한국 대상 발행인데 근거가 전부 외국어면 동명이의(同名異義) 주제일 가능성이 높다
        if str(self.config.get("trends.geo", "KR")).upper() == "KR" and not has_hangul:
            if not any(HANGUL.search(e.title or "") for e in topic.evidence):
                log.debug("한국어 근거가 없어 제외: %s", topic.title)
                return False

        max_age = int(self.config.get("trends.max_age_hours", 24))
        freshest = topic.freshest
        if freshest is not None:
            age = now_kst() - freshest.astimezone(now_kst().tzinfo)
            if age > timedelta(hours=max_age):
                log.debug("신선도 초과로 제외: %s (%s)", topic.title, age)
                return False

        return True

    # ---------------- 공개 API ----------------

    def top_topics(
        self,
        limit: int = 10,
        *,
        platform: str | None = None,
        exclude_keys: set[str] | None = None,
    ) -> list[Topic]:
        """발행 가능한 상위 주제를 점수순으로 반환.

        platform 을 주면 해당 플랫폼 기준으로 최근 중복 발행 주제를 걸러낸다.
        """
        topics = self._merge_similar(self.collect())
        topics = [t for t in topics if self._passes_filters(t)]

        exclude_keys = exclude_keys or set()
        dedupe_days = int(self.config.get("general.dedupe_days", 7))

        result: list[Topic] = []
        for topic in sorted(topics, key=lambda t: t.score, reverse=True):
            if topic.key in exclude_keys:
                continue
            if self.state and self.state.is_duplicate(
                topic.title, within_days=dedupe_days, platform=platform
            ):
                log.debug("최근 발행 이력으로 제외: %s", topic.title)
                continue
            result.append(topic)
            if len(result) >= limit:
                break

        log.info(
            "발행 후보 %d개 확보 (플랫폼=%s): %s",
            len(result),
            platform or "전체",
            ", ".join(t.title for t in result[:5]),
        )
        return result
