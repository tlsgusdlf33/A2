import pytest

from autopub.config import load_config
from autopub.publishers import TikTokPublisher, YouTubePublisher
from autopub.state import State


@pytest.fixture
def config():
    return load_config()


def test_youtube_daily_max_derived_from_free_quota(config, tmp_path):
    """무료 쿼터 10,000 ÷ 업로드 1,600 → 하루 6회가 상한."""
    config.data["platforms"]["youtube"]["daily_max"] = None
    publisher = YouTubePublisher(config, State(tmp_path / "s.json"))
    assert publisher.daily_max == 6


def test_youtube_daily_max_capped_by_quota(config, tmp_path):
    """쿼터로 불가능한 값을 설정해도 실제 상한으로 깎인다."""
    config.data["platforms"]["youtube"]["daily_max"] = 50
    publisher = YouTubePublisher(config, State(tmp_path / "s.json"))
    assert publisher.daily_max == 6


def test_quota_blocks_after_daily_max(config, tmp_path):
    state = State(tmp_path / "s.json")
    config.data["platforms"]["youtube"]["daily_max"] = 2
    config.data["platforms"]["youtube"]["min_gap_minutes"] = 0
    publisher = YouTubePublisher(config, state)

    assert publisher.check_ready()[0] is True
    state.record_publish("youtube", "a", "a")
    state.record_publish("youtube", "b", "b")
    ready, reason = publisher.check_ready()
    assert ready is False
    assert "소진" in reason


def test_min_gap_blocks_back_to_back(config, tmp_path):
    state = State(tmp_path / "s.json")
    config.data["platforms"]["tiktok"]["min_gap_minutes"] = 60
    publisher = TikTokPublisher(config, state)

    state.record_publish("tiktok", "a", "a")
    ready, reason = publisher.check_ready()
    assert ready is False
    assert "간격" in reason


def test_disabled_platform_is_not_ready(config, tmp_path):
    config.data["platforms"]["tiktok"]["enabled"] = False
    publisher = TikTokPublisher(config, State(tmp_path / "s.json"))
    assert publisher.check_ready()[0] is False
