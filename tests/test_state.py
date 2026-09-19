from datetime import timedelta

from autopub.state import State
from autopub.util import now_kst


def test_daily_count_and_dedupe(tmp_path):
    state = State(tmp_path / "s.json")
    state.record_publish("youtube", "삼성전자 3분기 실적", "제목", "https://x")

    assert state.count_today("youtube") == 1
    assert state.count_today("tiktok") == 0

    # 공백/기호/대소문자가 달라도 같은 주제로 본다
    assert state.is_duplicate("삼성전자  3분기 실적!", within_days=7)
    assert not state.is_duplicate("전혀 다른 주제", within_days=7)

    # 플랫폼을 지정하면 그 플랫폼 이력만 본다
    assert state.is_duplicate("삼성전자 3분기 실적", within_days=7, platform="youtube")
    assert not state.is_duplicate("삼성전자 3분기 실적", within_days=7, platform="tiktok")


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "s.json"
    first = State(path)
    first.record_publish("tistory", "주제", "제목", "https://u")
    first.save()

    assert State(path).count_today("tistory") == 1


def test_corrupt_file_does_not_crash(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{깨진 JSON", encoding="utf-8")
    assert State(path).count_today("youtube") == 0


def test_prune_drops_old_records(tmp_path):
    state = State(tmp_path / "s.json")
    state.record_publish("youtube", "옛날 주제", "제목")
    state.records[0]["published_at"] = (now_kst() - timedelta(days=200)).isoformat()
    state.record_publish("youtube", "최근 주제", "제목")

    assert state.prune(keep_days=90) == 1
    assert len(state.records) == 1


def test_minutes_since_last_without_history(tmp_path):
    assert State(tmp_path / "s.json").minutes_since_last("youtube") == float("inf")
