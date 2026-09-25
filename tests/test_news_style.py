"""뉴스 방송형(생성 이미지 + AI 앵커) 관련 테스트."""
from __future__ import annotations

import pytest
from PIL import Image

from autopub.config import load_config
from autopub.content.shorts import AI_DISCLOSURE, CardItem, CardScript, _clean_image_prompt
from autopub.media.broadcast import BroadcastSpec, render_broadcast_card
from autopub.media.imagegen import _cache_key, generate_image, scene_prompt
from autopub.publishers import TikTokPublisher, YouTubePublisher
from autopub.state import State
from autopub.trends.models import Evidence, Topic


# ---------------------------------------------------------------- 이미지 프롬프트 안전장치
#
# 뉴스 주제에는 실존 인물이 섞여 있다. 그 사람의 모습으로 '실제로 일어나지 않은
# 장면'을 만들어 뉴스처럼 내보내는 것이 이 기능의 가장 큰 위험이라,
# 사람이 들어간 프롬프트는 아예 버리고 앵커 화면으로 떨어뜨린다.

@pytest.mark.parametrize(
    "prompt",
    [
        "a politician speaking at a podium",
        "crowd of people at a rally",
        "portrait of a ceo in an office",
        "close up of a woman reading news",
    ],
)
def test_prompts_describing_people_are_rejected(prompt):
    assert _clean_image_prompt(prompt) == ""


def test_korean_prompt_is_rejected():
    """생성 모델이 한글을 제대로 못 알아들어 엉뚱한 그림이 나온다."""
    assert _clean_image_prompt("반도체 공장 생산라인") == ""


def test_too_short_prompt_is_rejected():
    assert _clean_image_prompt("city") == ""


def test_object_prompt_is_kept():
    prompt = "modern semiconductor factory clean room with rows of machines"
    assert _clean_image_prompt(prompt) == prompt


def test_scene_prompt_forbids_people():
    wrapped = scene_prompt("empty government building at dusk")
    assert "no people" in wrapped
    assert "no faces" in wrapped


# ---------------------------------------------------------------- AI 생성 고지
#
# 유튜브·틱톡 모두 합성 콘텐츠 표시를 요구한다. 무인 업로드라
# 사람이 매번 체크할 수 없으므로 코드가 자동으로 켜야 한다.

def _script(synthetic: bool) -> CardScript:
    return CardScript(
        title="오늘의 뉴스",
        hook="시작합니다.",
        items=[CardItem(name="항목", narration="내레이션.", caption="설명")],
        description="설명란",
        hashtags=["뉴스"],
        topic=Topic(title="t", evidence=[Evidence(title="근거", url="https://x")]),
        synthetic_media=synthetic,
    )


def test_disclosure_added_to_description_when_synthetic():
    assert _script(True).youtube_description().startswith(AI_DISCLOSURE)


def test_no_disclosure_when_not_synthetic():
    assert AI_DISCLOSURE not in _script(False).youtube_description()


def test_youtube_marks_synthetic_media(tmp_path):
    publisher = YouTubePublisher(load_config(), State(tmp_path / "s.json"))
    body = publisher.build_body(
        {"title": "제목", "description": "설명", "tags": ["a"], "synthetic_media": True}
    )
    assert body["status"]["containsSyntheticMedia"] is True


def test_youtube_omits_flag_for_normal_video(tmp_path):
    publisher = YouTubePublisher(load_config(), State(tmp_path / "s.json"))
    body = publisher.build_body({"title": "제목", "description": "설명", "tags": []})
    assert "containsSyntheticMedia" not in body["status"]


def test_tiktok_publisher_reads_aigc_flag(tmp_path):
    """post_info.is_aigc 가 payload 에서 온다 (업로드 없이 설정만 확인)."""
    publisher = TikTokPublisher(load_config(), State(tmp_path / "s.json"))
    assert publisher.platform == "tiktok"
    # 실제 전송 본문은 publish() 안에서 만들어지므로 소스에 플래그가 있는지 확인
    import inspect

    source = inspect.getsource(type(publisher).publish)
    assert "is_aigc" in source


# ---------------------------------------------------------------- 방송 카드

def test_broadcast_card_renders_without_image(tmp_path):
    """이미지 생성에 실패해도 카드가 만들어져야 파이프라인이 멈추지 않는다."""
    path = render_broadcast_card(
        BroadcastSpec(headline="헤드라인입니다", caption="설명 문장", kicker="속보",
                      ticker="하단 자막", image_path=None),
        tmp_path / "b.png", size=(1080, 1920),
    )
    with Image.open(path) as image:
        assert image.size == (1080, 1920)


def test_broadcast_card_handles_long_text(tmp_path):
    path = render_broadcast_card(
        BroadcastSpec(headline="아주 긴 헤드라인이 들어온 경우를 확인합니다 " * 3,
                      caption="설명도 매우 깁니다. " * 15, kicker="1위", ticker="티커"),
        tmp_path / "b.png",
    )
    with Image.open(path) as image:
        assert image.size == (1080, 1920)


# ---------------------------------------------------------------- 이미지 생성

def test_cache_key_is_stable_and_distinct():
    a = _cache_key("pollinations", "prompt", 1, (768, 1344))
    assert a == _cache_key("pollinations", "prompt", 1, (768, 1344))
    assert a != _cache_key("pollinations", "prompt", 2, (768, 1344))
    assert a != _cache_key("together", "prompt", 1, (768, 1344))


def test_unknown_provider_returns_none(tmp_path):
    """공급자 설정이 틀려도 예외로 죽지 않고 앵커 화면으로 떨어져야 한다."""
    assert generate_image("x", tmp_path / "o.jpg", provider="없는공급자") is None
