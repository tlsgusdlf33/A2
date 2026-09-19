from datetime import datetime, timedelta, timezone

from autopub.config import load_config
from autopub.state import State
from autopub.trends.aggregator import TrendAggregator, _same_topic
from autopub.trends.models import Evidence, Topic
from autopub.util import KST


def _topic(title, score=1.0, source="google_trends", evidence_title=None):
    return Topic(
        title=title,
        score=score,
        sources=[source],
        evidence=[
            Evidence(
                title=evidence_title or f"{title} 관련 기사",
                url="https://news.example/1",
                published_at=datetime.now(timezone.utc),
            )
        ],
    )


def test_same_topic_matches_keyword_inside_headline():
    assert _same_topic("최예나", "최예나, 신곡 발표 화제")
    assert not _same_topic("아스날", "삼성전자 실적 발표")


def test_merge_accumulates_score_across_sources():
    config = load_config()
    aggregator = TrendAggregator(config)
    merged = aggregator._merge_similar([
        _topic("최예나", 1.0, "google_trends"),
        _topic("최예나, 신곡 발표", 0.6, "news_rss"),
    ])
    assert len(merged) == 1
    assert merged[0].score == 1.6
    assert set(merged[0].sources) == {"google_trends", "news_rss"}


def test_blocklist_filters_topic():
    config = load_config()
    config.data["trends"]["blocklist_keywords"] = ["마약"]
    aggregator = TrendAggregator(config)
    assert not aggregator._passes_filters(_topic("유명인 마약 의혹"))


def test_topic_without_evidence_is_rejected():
    aggregator = TrendAggregator(load_config())
    assert not aggregator._passes_filters(Topic(title="근거 없는 주제"))


def test_short_ascii_acronym_is_rejected():
    aggregator = TrendAggregator(load_config())
    assert not aggregator._passes_filters(_topic("pl"))


def test_stale_topic_is_rejected():
    config = load_config()
    config.data["trends"]["max_age_hours"] = 6
    aggregator = TrendAggregator(config)

    topic = _topic("오래된 뉴스")
    topic.evidence[0].published_at = datetime.now(KST) - timedelta(hours=48)
    assert not aggregator._passes_filters(topic)


def test_recently_published_topic_is_excluded(tmp_path, monkeypatch):
    config = load_config()
    state = State(tmp_path / "s.json")
    state.record_publish("tistory", "이미 쓴 주제", "제목")

    aggregator = TrendAggregator(config, state)
    monkeypatch.setattr(aggregator, "collect", lambda: [_topic("이미 쓴 주제"), _topic("새로운 주제")])

    titles = [t.title for t in aggregator.top_topics(limit=5, platform="tistory")]
    assert "이미 쓴 주제" not in titles
    assert "새로운 주제" in titles
