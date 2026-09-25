import json

import pytest
from PIL import Image

from autopub.content.shorts import (
    CardItem,
    CardScript,
    _clean_visual,
    _keyword,
    generate_card_script,
)
from autopub.llm.base import LLMProvider
from autopub.media.cards import (
    CardSpec,
    Theme,
    _normalize_items,
    render_intro_card,
    render_item_card,
    wrap_text,
)
from autopub.media.charts import synth_candles
from autopub.trends.models import Evidence, Topic


# ----------------------------- visual 검증
# LLM 이 지어낸 수치로 그래프를 그리면 틀린 정보를 그럴듯하게 보여주게 된다.

def test_bars_rejected_when_values_not_numeric():
    assert _clean_visual({"type": "bars", "labels": ["A", "B"], "values": ["미상", "12"]}) == {}


def test_bars_rejected_when_lengths_mismatch():
    assert _clean_visual({"type": "bars", "labels": ["A", "B"], "values": [1]}) == {}


def test_bars_parses_comma_and_percent():
    result = _clean_visual({"type": "bars", "labels": ["A", "B"], "values": ["3,000", "12%"]})
    assert result["values"] == [3000.0, 12.0]


def test_unknown_visual_type_is_dropped():
    assert _clean_visual({"type": "파이차트", "text": "x"}) == {}
    assert _clean_visual("문자열") == {}


def test_keyword_cuts_on_word_boundary():
    assert _keyword("역대 최저치를 기록했습니다") == "역대 최저치를"
    assert _keyword("3배 급증") == "3배 급증"
    # 마침표는 떼고, 한도 안이면 그대로 둔다
    assert _keyword("전면 중단") == "전면 중단"


# ----------------------------- 대본 파싱

class _Fake(LLMProvider):
    name = "fake"

    def __init__(self, payload):
        super().__init__("fake", min_interval_sec=0, max_retries=1)
        self.payload = payload

    def _complete(self, system, prompt, *, json_mode):
        return json.dumps(self.payload, ensure_ascii=False)


def _payload(item_count=4, narration="짧은 내레이션입니다."):
    return {
        "title": "꼭 알아야 할 것들",
        "hook": "이거 모르면 손해입니다.",
        "items": [
            {"name": f"항목{i}", "narration": narration, "caption": f"설명 {i}",
             "visual": {"type": "text", "text": f"키워드{i}"}}
            for i in range(item_count)
        ],
        "outro": "정리하면 이렇습니다.",
        "description": "설명",
        "hashtags": ["이슈", "정리"],
    }


TOPIC = Topic(title="주제", evidence=[Evidence(title="근거 기사")])


def test_last_item_is_rank_one():
    """배열 마지막이 1위인 역순 카운트다운이어야 한다."""
    script = generate_card_script(_Fake(_payload(4)), TOPIC)
    assert [item.rank for item in script.items] == [4, 3, 2, 1]
    assert script.items[-1].rank_label == "1위"


def test_overlong_script_drops_lowest_ranks_and_renumbers():
    long_narration = "아주 긴 내레이션입니다. " * 12
    script = generate_card_script(
        _Fake(_payload(6, long_narration)), TOPIC, max_seconds=30
    )
    assert len(script.items) == 3
    # 1위는 반드시 살아남고 번호가 다시 매겨진다
    assert [item.rank for item in script.items] == [3, 2, 1]


def test_script_without_items_raises():
    with pytest.raises(ValueError):
        generate_card_script(_Fake({"title": "t", "hook": "h", "items": []}), TOPIC)


def test_narration_parts_follow_card_order():
    script = generate_card_script(_Fake(_payload(3)), TOPIC)
    # 인트로 + 항목 3개 + 아웃트로 = 5장
    assert len(script.narration_parts) == 5
    assert script.narration_parts[0].startswith("이거 모르면")


# ----------------------------- 레이아웃

def test_wrap_text_breaks_long_korean_without_spaces():
    image = Image.new("RGB", (10, 10))
    from PIL import ImageDraw

    from autopub.media.fonts import load_font

    draw = ImageDraw.Draw(image)
    font = load_font(40)
    lines = wrap_text(draw, "가나다라마바사아자차카타파하" * 4, font, 300)
    assert len(lines) > 1
    assert all(draw.textlength(line, font=font) <= 300 for line in lines)


def test_normalize_items_accepts_strings_and_dicts():
    result = _normalize_items(["가", {"name": "나", "visual": {"type": "text"}}, "", None])
    assert [item["name"] for item in result] == ["가", "나"]


# ----------------------------- 렌더링

def test_item_card_renders_at_requested_size(tmp_path):
    path = render_item_card(
        CardSpec(rank="1위", heading="제목", caption="설명 문장입니다.",
                 visual={"type": "text", "text": "강조"}),
        tmp_path / "c.png", Theme.preset("cream"), (1080, 1920),
    )
    with Image.open(path) as image:
        assert image.size == (1080, 1920)


def test_intro_card_renders(tmp_path):
    path = render_intro_card(
        "절대 하면 안되는 3가지", ["하나", "둘", "셋"], tmp_path / "i.png",
        Theme.preset("mint"), (1080, 1920),
    )
    with Image.open(path) as image:
        assert image.size == (1080, 1920)


def test_card_survives_very_long_caption(tmp_path):
    """긴 설명이 들어와도 카드가 깨지지 않아야 한다."""
    path = render_item_card(
        CardSpec(rank="2위", heading="아주아주 긴 제목이 들어온 경우입니다",
                 caption="설명이 매우 깁니다. " * 20),
        tmp_path / "c.png",
    )
    with Image.open(path) as image:
        assert image.size == (1080, 1920)


def test_themes_are_distinct():
    assert Theme.preset("cream").background != Theme.preset("charcoal").background
    # 알 수 없는 이름은 기본값으로 떨어진다
    assert Theme.preset("없는테마").background == Theme.preset("cream").background


# ----------------------------- 차트

def test_synth_candles_shooting_star_has_long_upper_wick():
    candles = synth_candles("슈팅스타")
    star = candles[-3]
    upper = star.high - max(star.open, star.close)
    body = abs(star.close - star.open)
    assert upper > body * 2


def test_synth_candles_is_deterministic():
    assert [c.close for c in synth_candles("도지")] == [c.close for c in synth_candles("도지")]
