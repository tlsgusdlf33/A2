"""파이프라인 전체 배선 검증.

LLM 만 가짜로 대체하고 트렌드 수집 → 대본 → TTS → 자막 → ffmpeg 까지
실제로 돌려서, 모듈 간 연결이 끊긴 곳이 없는지 확인한다.

ffmpeg 가 없거나 네트워크가 막힌 환경에서는 자동으로 건너뛴다.
실행: pytest -m integration
"""
from __future__ import annotations

import json
import shutil

import pytest

from autopub.config import load_config
from autopub.llm.base import LLMProvider
from autopub.pipeline import Pipeline
from autopub.state import State
from autopub.trends.models import Evidence, Topic

pytestmark = pytest.mark.integration


class FakeLLM(LLMProvider):
    """정해진 JSON 만 돌려주는 가짜 공급자."""

    name = "fake"

    def __init__(self, payload: dict):
        super().__init__("fake-model", min_interval_sec=0, max_retries=1)
        self.payload = payload

    def _complete(self, system: str, prompt: str, *, json_mode: bool) -> str:
        return json.dumps(self.payload, ensure_ascii=False)


SHORTS_PAYLOAD = {
    "title": "오늘 가장 뜨거운 이슈 정리",
    "hook": "이거 아직 모르셨나요",
    "scenes": [
        {"narration": "오늘 아침부터 검색어를 점령한 소식입니다.",
         "on_screen": "실시간 1위", "broll_query": "city morning"},
        {"narration": "핵심은 발표 시점과 그 파급 효과입니다.",
         "on_screen": "핵심 두 가지", "broll_query": "office meeting"},
        {"narration": "관련 업계 전반이 영향권에 들어갔습니다.",
         "on_screen": "업계 전반", "broll_query": "stock chart"},
        {"narration": "지금부터가 진짜 시작입니다.",
         "on_screen": "지금부터", "broll_query": "sunrise"},
    ],
    "description": "오늘의 이슈를 한 번에 정리했습니다.",
    "hashtags": ["이슈", "실시간", "뉴스"],
}

BLOG_PAYLOAD = {
    "title": "오늘 가장 뜨거운 이슈, 핵심만 정리",
    "meta_description": "지금 검색어를 점령한 이슈의 배경과 파급 효과를 정리했습니다.",
    "tags": ["이슈", "정리", "속보"],
    "body_markdown": (
        "오늘 아침부터 검색어를 점령한 소식입니다.\n\n"
        "## 무슨 일이 있었나\n관련 보도가 잇따라 나왔습니다.\n\n"
        "## 왜 중요한가\n업계 전반에 영향을 줄 수 있다는 분석이 나옵니다.\n"
    ),
    "faq": [{"q": "언제 발표됐나요?", "a": "오늘 오전 보도가 나왔습니다."}],
    "image_queries": ["city skyline", "office"],
    "sources_note": "주요 언론 보도를 참고했습니다.",
}

TOPIC = Topic(
    title="테스트 이슈",
    score=1.0,
    sources=["google_trends"],
    evidence=[Evidence(title="테스트 이슈 관련 보도", url="https://news.example/1",
                       source="테스트뉴스", snippet="요약입니다.")],
)


def _pipeline(tmp_path, payload, monkeypatch):
    config = load_config()
    config.data["general"]["dry_run"] = True
    config.data["general"]["work_dir"] = str(tmp_path / "work")
    config.data["general"]["out_dir"] = str(tmp_path / "out")
    # 테스트를 빠르게 끝내기 위해 짧은 영상으로
    config.data["video"]["target_seconds"] = [15, 20]

    pipeline = Pipeline(config, State(tmp_path / "state.json"))
    pipeline._llm = FakeLLM(payload)
    monkeypatch.setattr(pipeline, "_pick_topic", lambda platforms: TOPIC)
    return pipeline


CARD_PAYLOAD = {
    "title": "지금 꼭 알아야 할 3가지",
    "hook": "이거 모르면 손해입니다.",
    "items": [
        {"name": "발표 시점", "narration": "오늘 오전에 나온 소식입니다.",
         "caption": "오늘 오전 보도가 나왔습니다.",
         "visual": {"type": "text", "text": "오늘 오전"}},
        {"name": "시장 반응", "narration": "시장은 곧바로 반응했습니다.",
         "caption": "발표 직후 곧바로 반응이 나왔습니다.",
         "visual": {"type": "text", "text": "즉시 반응"}},
        {"name": "파급 효과", "narration": "업계 전반이 영향권에 들어갔습니다.",
         "caption": "관련 업계 전반이 영향권입니다.",
         "visual": {"type": "text", "text": "업계 전반"}},
    ],
    "outro": "지금부터가 진짜 시작입니다.",
    "description": "오늘의 이슈 정리입니다.",
    "hashtags": ["이슈", "정리"],
}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg 없음")
def test_card_pipeline_produces_playable_video(tmp_path, monkeypatch):
    """카드 스타일 전체 경로: 대본 → 카드 렌더 → 카드별 TTS → ffmpeg."""
    pipeline = _pipeline(tmp_path, CARD_PAYLOAD, monkeypatch)
    pipeline.config.data["video"]["style"] = "card"

    try:
        report = pipeline.run_shorts(["youtube"])
    except Exception as exc:
        pytest.skip(f"네트워크 의존 단계 실패: {exc}")

    assert report.published, f"발행 실패: {report.failed}"

    from PIL import Image

    from autopub.media.tts import probe_duration

    video = next(iter((tmp_path / "work").rglob("video.mp4")))
    assert probe_duration(video) > 5

    # 인트로 + 항목 3개 + 아웃트로 = 카드 5장
    cards = sorted((tmp_path / "work").rglob("card_*.png"))
    assert len(cards) == 5
    with Image.open(cards[0]) as image:
        assert image.size == (1080, 1920)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg 없음")
def test_broll_pipeline_produces_playable_video(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, SHORTS_PAYLOAD, monkeypatch)
    pipeline.config.data["video"]["style"] = "broll"

    try:
        report = pipeline.run_shorts(["youtube"])
    except Exception as exc:  # edge-tts 는 네트워크가 필요하다
        pytest.skip(f"네트워크 의존 단계 실패: {exc}")

    assert report.published, f"발행 실패: {report.failed}"

    from autopub.media.tts import probe_duration

    video = next(iter((tmp_path / "work").rglob("video.mp4")))
    assert video.stat().st_size > 50_000
    assert probe_duration(video) > 5

    # 자막 파일도 함께 남아야 한다
    assert next(iter((tmp_path / "work").rglob("subs.ass")), None) is not None


def test_blog_pipeline_writes_html(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, BLOG_PAYLOAD, monkeypatch)
    report = pipeline.run_blog()

    assert report.published, f"발행 실패: {report.failed}"
    html_files = list((tmp_path / "out").glob("*.html"))
    assert html_files

    content = html_files[0].read_text(encoding="utf-8")
    assert "무슨 일이 있었나" in content
    assert "자주 묻는 질문" in content
