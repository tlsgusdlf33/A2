"""진행자 스타일(진행자 클립 + 오버레이) 테스트."""
from __future__ import annotations

import json

import pytest
from PIL import Image

from autopub.content.shorts import generate_card_script
from autopub.llm.base import LLMProvider
from autopub.media.overlay import ACCENTS, OverlaySpec, render_overlay
from autopub.media.presenter import PresenterScene, _global_cues
from autopub.media.subtitles import write_ass
from autopub.media.tts import WordTiming
from autopub.trends.models import Evidence, Topic


# ---------------------------------------------------------------- 자막 타임라인
#
# 장면마다 TTS 를 따로 돌리므로 각 단어 타이밍은 '그 장면 안에서의' 시각이다.
# 누적 오프셋을 더하지 않으면 두 번째 장면부터 자막이 전부 앞당겨진다.

def _scene(duration: float, words: list[tuple[str, float, float]]) -> PresenterScene:
    return PresenterScene(
        overlay_path=None, audio_path=None, duration=duration,
        words=[WordTiming(text=t, start=s, end=e) for t, s, e in words],
    )


def test_cues_are_offset_by_previous_scenes():
    scenes = [
        _scene(5.0, [("가", 0.0, 1.0)]),
        _scene(4.0, [("나", 0.0, 1.0)]),
        _scene(3.0, [("다", 0.0, 1.0)]),
    ]
    cues = _global_cues(scenes, hold_seconds=0.5, max_chars=20)

    assert len(cues) == 3
    assert cues[0].start == pytest.approx(0.0)
    # 5.0 + 0.5 홀드
    assert cues[1].start == pytest.approx(5.5)
    # 5.0 + 0.5 + 4.0 + 0.5
    assert cues[2].start == pytest.approx(10.0)


def test_cues_stay_in_order_and_do_not_go_backwards():
    scenes = [_scene(4.0, [("가", 0.2, 1.2), ("나", 1.3, 2.4)]) for _ in range(4)]
    cues = _global_cues(scenes, hold_seconds=0.3, max_chars=4)
    starts = [cue.start for cue in cues]
    assert starts == sorted(starts)


def test_scene_without_words_still_advances_the_clock():
    """TTS 단어 타이밍이 비어도 뒤 장면 자막이 밀려야 한다."""
    scenes = [_scene(6.0, []), _scene(3.0, [("다", 0.0, 1.0)])]
    cues = _global_cues(scenes, hold_seconds=0.5, max_chars=20)
    assert len(cues) == 1
    assert cues[0].start == pytest.approx(6.5)


# ---------------------------------------------------------------- 오버레이

def test_overlay_is_transparent_and_correct_size(tmp_path):
    path = render_overlay(
        OverlaySpec(kicker="머리글", headline="헤드라인 강조", highlight="강조",
                    accent="blue", card_value="4.0조원", card_label="설명"),
        tmp_path / "o.png", size=(1080, 1920),
    )
    with Image.open(path) as image:
        assert image.size == (1080, 1920)
        assert image.mode == "RGBA"
        # 좌상단 모서리는 비어 있어야 진행자 영상이 비친다
        assert image.getpixel((5, 5))[3] == 0


def test_overlay_without_card_renders(tmp_path):
    spec = OverlaySpec(kicker="머리글", headline="제목만")
    assert spec.has_card is False
    path = render_overlay(spec, tmp_path / "o.png")
    with Image.open(path) as image:
        assert image.size == (1080, 1920)


def test_highlight_not_in_headline_does_not_crash(tmp_path):
    """LLM 이 name 에 없는 문자열을 highlight 로 주는 경우."""
    path = render_overlay(
        OverlaySpec(headline="로보티즈 시총 4조", highlight="존재하지않음"),
        tmp_path / "o.png",
    )
    assert path.exists()


def test_unknown_accent_falls_back_to_red():
    assert OverlaySpec(accent="무지개").accent_color == ACCENTS["red"]


def test_long_headline_and_card_still_fit(tmp_path):
    path = render_overlay(
        OverlaySpec(kicker="아주 긴 머리글입니다 " * 3,
                    headline="아주 긴 헤드라인이 들어온 경우입니다 " * 2,
                    card_value="매우 긴 수치 문자열", card_label="설명이 아주 깁니다 " * 6),
        tmp_path / "o.png",
    )
    with Image.open(path) as image:
        assert image.size == (1080, 1920)


# ---------------------------------------------------------------- highlight 검증

class _Fake(LLMProvider):
    name = "fake"

    def __init__(self, payload):
        super().__init__("fake", min_interval_sec=0, max_retries=1)
        self.payload = payload

    def _complete(self, system, prompt, *, json_mode):
        return json.dumps(self.payload, ensure_ascii=False)


def _payload(highlight: str):
    return {
        "title": "제목",
        "hook": "훅입니다.",
        "items": [{
            "name": "로보티즈 시총 4조",
            "narration": "내레이션입니다.",
            "caption": "설명",
            "highlight": highlight,
            "visual": {"type": "text", "text": "4조원"},
        }],
        "outro": "정리",
        "hashtags": ["주식"],
    }


TOPIC = Topic(title="주제", evidence=[Evidence(title="근거")])


def test_highlight_kept_when_substring_of_name():
    script = generate_card_script(_Fake(_payload("시총 4조")), TOPIC)
    assert script.items[0].highlight == "시총 4조"


def test_highlight_dropped_when_not_in_name():
    """name 에 없는 강조 문자열은 화면에서 아무 효과가 없으므로 버린다."""
    script = generate_card_script(_Fake(_payload("전혀 다른 말")), TOPIC)
    assert script.items[0].highlight == ""


# ---------------------------------------------------------------- 자막 스타일

def _style_fields(path) -> dict[str, str]:
    """.ass 의 Format: 줄을 읽어 Style 값을 이름으로 꺼낸다.

    필드 위치를 숫자로 박아 두면 스타일 항목이 하나 바뀔 때 조용히 엉뚱한
    값을 검사하게 된다.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    names = next(
        l.split(":", 1)[1] for l in lines if l.startswith("Format:") and "Fontname" in l
    )
    keys = [name.strip() for name in names.split(",")]
    values = next(l for l in lines if l.startswith("Style:")).split(":", 1)[1]
    return dict(zip(keys, [value.strip() for value in values.split(",")]))


def test_boxed_subtitle_uses_opaque_box_border(tmp_path):
    from autopub.media.subtitles import Cue

    path = write_ass([Cue("자막", 0, 1)], tmp_path / "s.ass", box=True)
    fields = _style_fields(path)
    # BorderStyle 3 = 글자 뒤 상자
    assert fields["BorderStyle"] == "3"
    # BackColour 알파가 완전 불투명(00)이 아니어야 배경이 비친다
    assert fields["BackColour"][2:4] != "00"


def test_plain_subtitle_uses_outline_border(tmp_path):
    from autopub.media.subtitles import Cue

    path = write_ass([Cue("자막", 0, 1)], tmp_path / "s.ass", box=False)
    assert _style_fields(path)["BorderStyle"] == "1"


# ---------------------------------------------------------------- 외형 조합
#
# 얼굴/의상/배경/구도를 따로 두는 이유는 "이 얼굴 그대로 옷만 바꿔줘"가 되게 하기 위함이다.

def test_look_swaps_outfit_while_keeping_face():
    from autopub.media.looks import FACES, resolve

    anchor = resolve("anchor")
    street = resolve("anchor", outfit="street_knit", scene="street_bench")
    # 얼굴 묘사는 그대로, 의상만 달라진다
    assert FACES[anchor.face] in street.prompt()
    assert "BLACK lace-trim knit" in street.prompt()
    assert "NAVY business blazer" not in street.prompt()


def test_outfit_appears_before_scene_in_prompt():
    """프롬프트가 길어지면 뒤쪽 토큰이 무시된다. 의상은 반드시 배경보다 앞이어야 한다."""
    from autopub.media.looks import resolve

    prompt = resolve("street").prompt()
    assert prompt.index("wearing") < prompt.index("sunny Korean street")


def test_unknown_face_falls_back_without_crashing():
    from autopub.media.looks import FACES, resolve

    look = resolve("street", face="없는얼굴")
    assert FACES["4"] in look.prompt()


def test_resolve_overrides_only_given_fields():
    from autopub.media.looks import resolve

    base = resolve("street")
    changed = resolve("street", seed=99)
    assert changed.seed == 99
    assert changed.outfit == base.outfit
    assert changed.face == base.face
