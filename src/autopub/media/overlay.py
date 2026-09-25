"""진행자 영상 위에 얹는 오버레이.

레퍼런스 채널의 구성은 세 겹이다.
  상단  : 작은 머리글 + 큰 2색 헤드라인
  중앙  : 큰 흰 자막 (내레이션에 맞춰 바뀜 — ASS 로 별도 처리)
  카드  : 흰 둥근 박스 + 컬러 테두리 + 큰 수치

이 모듈은 상단과 카드를 투명 PNG 한 장으로 그린다. 장면마다 한 장씩 만들어
ffmpeg 이 해당 구간에만 덧씌운다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from ..logutil import get_logger
from ..util import ensure_dir
from .cards import fit_font, wrap_text
from .fonts import load_font

log = get_logger(__name__)

Color = tuple[int, int, int]
RGBA = tuple[int, int, int, int]

# 레퍼런스의 강조색
ACCENT_RED = (228, 30, 38)
ACCENT_BLUE = (28, 104, 214)
ACCENT_YELLOW = (240, 190, 20)
WHITE = (255, 255, 255)
INK = (24, 24, 28)
MUTED = (110, 112, 120)

ACCENTS = {"red": ACCENT_RED, "blue": ACCENT_BLUE, "yellow": ACCENT_YELLOW}


@dataclass
class OverlaySpec:
    """한 장면의 오버레이."""

    kicker: str = ""            # 상단 작은 머리글
    headline: str = ""          # 상단 큰 글씨
    highlight: str = ""         # headline 중 강조색으로 칠할 부분
    accent: str = "red"         # red | blue | yellow
    card_value: str = ""        # 카드 큰 수치 ("4.0조원")
    card_label: str = ""        # 카드 작은 설명
    card_badge: str = ""        # 카드 상단 작은 라벨 ("로보티즈")

    @property
    def accent_color(self) -> Color:
        return ACCENTS.get(self.accent, ACCENT_RED)

    @property
    def has_card(self) -> bool:
        return bool(self.card_value.strip() or self.card_label.strip())


def _shadow_text(draw, xy, text, font, fill: Color, shadow: RGBA = (0, 0, 0, 200)) -> None:
    """야외 촬영 배경에서도 읽히도록 글자에 그림자를 깐다."""
    x, y = xy
    for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2), (-2, 2), (2, -2)):
        draw.text((x + dx, y + dy), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def _draw_two_tone_headline(
    draw, *, width: int, top: int, headline: str, highlight: str, accent: Color,
) -> int:
    """헤드라인을 그리되 highlight 부분만 강조색으로 칠한다.

    레퍼런스처럼 '로보티즈 시총 4조' 에서 '시총 4조' 만 파랗게 하는 식이다.
    한 줄에 들어가야 효과가 사니, 안 들어가면 글자를 줄인다.
    """
    font, lines = fit_font(draw, headline, width - 80, 200, 92, min_size=54)
    y = top

    for line in lines:
        # 이 줄에 강조 문자열이 통째로 들어 있을 때만 분할해서 칠한다
        index = line.find(highlight) if highlight else -1
        if index < 0:
            line_width = draw.textlength(line, font=font)
            _shadow_text(draw, (width / 2 - line_width / 2, y), line, font, WHITE)
        else:
            head, mid, tail = line[:index], highlight, line[index + len(highlight):]
            widths = [draw.textlength(part, font=font) for part in (head, mid, tail)]
            x = width / 2 - sum(widths) / 2
            for part, part_width, color in zip(
                (head, mid, tail), widths, (WHITE, accent, WHITE)
            ):
                if part:
                    _shadow_text(draw, (x, y), part, font, color)
                x += part_width
        y += font.size * 1.18

    return int(y)


def _draw_card(
    draw, *, width: int, top: int, spec: OverlaySpec, margin: int = 70,
) -> None:
    """흰 둥근 박스 + 컬러 테두리 + 큰 수치."""
    accent = spec.accent_color
    inner_width = width - margin * 2 - 60

    badge_font = load_font(34, bold=True)
    value_font, value_lines = fit_font(draw, spec.card_value, inner_width, 130, 84, min_size=44)
    label_font = load_font(34, bold=True)
    label_lines = wrap_text(draw, spec.card_label, label_font, inner_width) if spec.card_label else []

    height = 44
    if spec.card_badge:
        height += badge_font.size + 16
    if spec.card_value:
        height += len(value_lines) * value_font.size * 1.15
    if label_lines:
        height += len(label_lines) * label_font.size * 1.25 + 8

    draw.rounded_rectangle(
        [margin, top, width - margin, top + height],
        radius=22, fill=(255, 255, 255, 250), outline=(*accent, 255), width=5,
    )

    y = top + 22
    if spec.card_badge:
        badge_width = draw.textlength(spec.card_badge, font=badge_font)
        draw.text((width / 2 - badge_width / 2, y), spec.card_badge,
                  font=badge_font, fill=(*MUTED, 255))
        y += badge_font.size + 16

    for line in value_lines:
        if not spec.card_value:
            break
        line_width = draw.textlength(line, font=value_font)
        draw.text((width / 2 - line_width / 2, y), line, font=value_font, fill=(*accent, 255))
        y += value_font.size * 1.15

    for line in label_lines:
        line_width = draw.textlength(line, font=label_font)
        draw.text((width / 2 - line_width / 2, y), line, font=label_font, fill=(*INK, 255))
        y += label_font.size * 1.25


def render_overlay(
    spec: OverlaySpec,
    out_path: str | Path,
    *,
    size: tuple[int, int] = (1080, 1920),
    card_top_ratio: float = 0.66,
) -> Path:
    """투명 배경 오버레이 PNG 를 만든다.

    card_top_ratio 는 자막 띠와 겹치지 않게 잡아야 한다.
    자막이 margin_v=820 이면 띠 아래끝이 약 1100px 이라
    0.66(=1267px) 아래로 두면 안전하다.
    """
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    y = 120
    if spec.kicker:
        kicker_font = load_font(38, bold=True)
        kicker_width = draw.textlength(spec.kicker, font=kicker_font)
        _shadow_text(draw, (width / 2 - kicker_width / 2, y), spec.kicker, kicker_font, WHITE)
        y += kicker_font.size + 18

    if spec.headline:
        _draw_two_tone_headline(
            draw, width=width, top=y, headline=spec.headline,
            highlight=spec.highlight, accent=spec.accent_color,
        )

    if spec.has_card:
        _draw_card(draw, width=width, top=int(height * card_top_ratio), spec=spec)

    target = Path(out_path)
    ensure_dir(target.parent)
    image.save(target)
    return target
