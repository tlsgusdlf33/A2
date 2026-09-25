"""숏폼 카드 렌더러.

스톡 영상 위에 자막을 얹는 대신, 단색 배경 위에 직접 그린 카드를 쓴다.
- 주제와 화면이 항상 일치한다 (스톡 b-roll 의 '템플릿 티'가 사라진다)
- 채널 고유의 톤이 생긴다
- 외부 API 없이 무료로 무한 생성된다

카드는 두 종류.
  인트로 카드 : 큰 제목 + 항목 미리보기 그리드
  항목 카드   : 순위 뱃지 + 항목명 + 그래픽 + 설명 박스
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from ..logutil import get_logger
from ..util import ensure_dir
from .charts import draw_bars, draw_candles, synth_candles
from .fonts import load_font

log = get_logger(__name__)

Color = tuple[int, int, int]


@dataclass
class Theme:
    """카드 배색. 참고 레퍼런스의 크림톤을 기본으로 한다."""

    background: Color = (247, 233, 200)
    surface: Color = (252, 244, 224)
    text: Color = (24, 24, 24)
    muted: Color = (92, 88, 78)
    border: Color = (38, 36, 32)
    accent: Color = (222, 170, 30)

    @classmethod
    def preset(cls, name: str) -> "Theme":
        presets = {
            "cream": cls(),
            "mint": cls(
                background=(226, 240, 227), surface=(240, 249, 240),
                border=(30, 62, 44), accent=(224, 122, 46),
            ),
            "lemon": cls(
                background=(233, 242, 191), surface=(243, 249, 214),
                border=(44, 52, 24), accent=(216, 86, 40),
            ),
            "sky": cls(
                background=(219, 234, 246), surface=(238, 246, 252),
                border=(24, 48, 78), accent=(226, 116, 38),
            ),
            "charcoal": cls(
                background=(28, 30, 34), surface=(44, 47, 52), text=(244, 244, 244),
                muted=(170, 172, 178), border=(120, 124, 132), accent=(255, 196, 60),
            ),
        }
        return presets.get(name, presets["cream"])


@dataclass
class CardSpec:
    """한 장면을 그리는 데 필요한 모든 것."""

    caption: str
    rank: str = ""            # "3위" — 비우면 뱃지를 그리지 않는다
    heading: str = ""         # 항목명 ("슈팅스타")
    kicker: str = ""          # 상단 작은 글씨 (주제명 등)
    visual: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# 텍스트 레이아웃
# --------------------------------------------------------------------------

def wrap_text(draw, text: str, font, max_width: float) -> list[str]:
    """한국어 줄바꿈.

    한글은 어절이 길어 공백 단위로만 자르면 한 줄이 넘친다.
    공백 단위로 먼저 시도하고, 그래도 넘치면 글자 단위로 쪼갠다.
    """
    lines: list[str] = []
    for paragraph in str(text).split("\n"):
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            # 단어 하나가 한 줄보다 길면 글자 단위로 자른다
            if draw.textlength(word, font=font) > max_width:
                chunk = ""
                for char in word:
                    if draw.textlength(chunk + char, font=font) <= max_width:
                        chunk += char
                    else:
                        lines.append(chunk)
                        chunk = char
                current = chunk
            else:
                current = word
        if current:
            lines.append(current)
    return lines or [""]


def draw_centered_block(
    draw, text: str, font, *, center_x: float, top: float,
    max_width: float, fill: Color, line_gap: float = 1.24,
) -> float:
    """가운데 정렬 텍스트 블록을 그리고 마지막 y 를 돌려준다."""
    lines = wrap_text(draw, text, font, max_width)
    line_height = font.size * line_gap
    y = top
    for line in lines:
        width = draw.textlength(line, font=font)
        draw.text((center_x - width / 2, y), line, font=font, fill=fill)
        y += line_height
    return y


def fit_font(draw, text: str, max_width: float, max_height: float,
             start_size: int, *, min_size: int = 28, bold: bool = True):
    """주어진 상자에 들어갈 때까지 글자 크기를 줄인다."""
    size = start_size
    while size > min_size:
        font = load_font(size, bold=bold)
        lines = wrap_text(draw, text, font, max_width)
        if len(lines) * font.size * 1.24 <= max_height:
            return font, lines
        size -= 4
    font = load_font(min_size, bold=bold)
    return font, wrap_text(draw, text, font, max_width)


# --------------------------------------------------------------------------
# 그래픽 영역
# --------------------------------------------------------------------------

def _draw_visual(draw, image: Image.Image, box: tuple[int, int, int, int],
                 visual: dict, theme: Theme) -> None:
    """장면의 시각 요소를 박스 안에 그린다."""
    kind = str(visual.get("type", "")).lower()
    left, top, right, bottom = box

    if kind == "candles":
        # 미리보기용 작은 박스에서는 강조 장식이 오히려 산만하다
        compact = (bottom - top) < 320
        count = int(visual.get("count", 7 if compact else 18))
        candles = synth_candles(
            str(visual.get("pattern", visual.get("label", "default"))), count=count
        )
        if compact:
            draw_candles(draw, box, candles, highlight_index=None,
                         accent=theme.accent, text_color=theme.text)
        else:
            index = visual.get("highlight_index")
            draw_candles(
                draw, box, candles,
                highlight_index=len(candles) - 3 if index is None else int(index),
                highlight_label=str(visual.get("highlight_label", "")),
                accent=theme.accent, text_color=theme.text,
            )

    elif kind == "bars":
        values = [float(v) for v in visual.get("values", [])]
        labels = [str(x) for x in visual.get("labels", [])]
        if values and len(labels) == len(values):
            draw_bars(draw, box, values, labels,
                      accent=theme.border, text_color=theme.text)

    elif kind == "image":
        path = visual.get("path")
        if path and Path(path).exists():
            _paste_cover(image, Path(path), box, radius=28)

    elif kind in ("text", "keyword", ""):
        # 그래픽이 없으면 강조 문구를 크게 세운다
        text = str(visual.get("text", "")).strip()
        if text:
            font, lines = fit_font(
                draw, text, (right - left) - 80, (bottom - top) - 80, 150, min_size=52
            )
            total = len(lines) * font.size * 1.24
            y = top + ((bottom - top) - total) / 2
            for line in lines:
                width = draw.textlength(line, font=font)
                draw.text(((left + right) / 2 - width / 2, y), line,
                          font=font, fill=theme.text)
                y += font.size * 1.24


def _paste_cover(canvas: Image.Image, path: Path, box: tuple[int, int, int, int],
                 radius: int = 28) -> None:
    """이미지를 박스에 꽉 차게(cover) 넣고 모서리를 둥글린다."""
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    try:
        photo = Image.open(path).convert("RGB")
    except OSError as exc:
        log.warning("이미지를 열지 못했습니다 (%s): %s", path, exc)
        return

    scale = max(width / photo.width, height / photo.height)
    resized = photo.resize(
        (max(1, int(photo.width * scale)), max(1, int(photo.height * scale))),
        Image.LANCZOS,
    )
    ox = (resized.width - width) // 2
    oy = (resized.height - height) // 2
    cropped = resized.crop((ox, oy, ox + width, oy + height))

    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, width - 1, height - 1],
                                           radius=radius, fill=255)
    canvas.paste(cropped, (left, top), mask)


# --------------------------------------------------------------------------
# 카드
# --------------------------------------------------------------------------

def _normalize_items(items: list) -> list[dict]:
    """미리보기 항목을 {name, visual} 형태로 통일한다.

    호출부가 문자열 목록을 주든 dict 목록을 주든 받아들인다.
    """
    normalized: list[dict] = []
    for item in items or []:
        if not item:
            continue        # None 을 str() 하면 "None" 이라는 항목이 생긴다
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("heading") or "").strip()
            if name:
                normalized.append({"name": name, "visual": item.get("visual") or {}})
        elif isinstance(item, str) and item.strip():
            normalized.append({"name": item.strip(), "visual": {}})
    return normalized


def _new_canvas(theme: Theme, size: tuple[int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", size, theme.background)
    return image, ImageDraw.Draw(image)


def _estimate_caption_height(draw, text: str, width: int, margin: int) -> int:
    """설명 박스가 차지할 높이를 미리 계산한다 (그래픽 박스 크기를 정하기 위해)."""
    if not str(text).strip():
        return 0
    inner = width - margin * 2 - 64
    font, lines = fit_font(draw, text, inner, 400, 50, min_size=32)
    return int(len(lines) * font.size * 1.3 + 56)


def _caption_box(draw, text: str, theme: Theme, *, width: int,
                 top: int, margin: int, max_height: int) -> None:
    """하단 설명 박스. 레퍼런스의 둥근 테두리 말풍선."""
    if not text.strip():
        return
    inner = width - margin * 2 - 64
    font, lines = fit_font(draw, text, inner, max_height - 56, 50, min_size=32)
    text_height = len(lines) * font.size * 1.3
    box_height = text_height + 56

    draw.rounded_rectangle(
        [margin, top, width - margin, top + box_height],
        radius=28, fill=theme.surface, outline=theme.border, width=4,
    )
    y = top + 28
    for line in lines:
        line_width = draw.textlength(line, font=font)
        draw.text((width / 2 - line_width / 2, y), line, font=font, fill=theme.text)
        y += font.size * 1.3


def render_item_card(
    spec: CardSpec, out_path: str | Path, theme: Theme | None = None,
    size: tuple[int, int] = (1080, 1920),
) -> Path:
    """순위 뱃지 + 항목명 + 그래픽 + 설명 박스."""
    theme = theme or Theme()
    width, height = size
    margin = 64
    image, draw = _new_canvas(theme, size)

    y = 150

    if spec.kicker:
        font = load_font(40, bold=True)
        text_width = draw.textlength(spec.kicker, font=font)
        draw.text((width / 2 - text_width / 2, y), spec.kicker,
                  font=font, fill=theme.muted)
        y += 70

    if spec.rank:
        font = load_font(76, bold=True)
        text_width = draw.textlength(spec.rank, font=font)
        box_w, box_h = text_width + 76, 118
        draw.rounded_rectangle(
            [width / 2 - box_w / 2, y, width / 2 + box_w / 2, y + box_h],
            radius=24, outline=theme.border, width=6,
        )
        draw.text((width / 2 - text_width / 2, y + 16), spec.rank,
                  font=font, fill=theme.text)
        y += box_h + 44

    if spec.heading:
        font, lines = fit_font(draw, spec.heading, width - margin * 2, 230, 104, min_size=56)
        for line in lines:
            line_width = draw.textlength(line, font=font)
            draw.text((width / 2 - line_width / 2, y), line, font=font, fill=theme.text)
            y += font.size * 1.2
        y += 34

    # 그래픽 박스 — 남는 세로 공간을 채운다.
    # 숏폼 UI(하단 재생바·계정명)가 가리는 아래쪽 300px 정도는 비워 둔다.
    caption_height = _estimate_caption_height(draw, spec.caption, width, margin)
    safe_bottom = height - 300
    visual_top = int(y)
    visual_bottom = int(safe_bottom - caption_height - 48)
    if visual_bottom - visual_top > 180:
        box = (margin, visual_top, width - margin, visual_bottom)
        if spec.visual.get("type") in ("candles", "bars"):
            draw.rounded_rectangle(list(box), radius=32,
                                   fill=theme.surface, outline=theme.border, width=4)
        _draw_visual(draw, image, box, spec.visual, theme)
        y = visual_bottom + 48

    _caption_box(draw, spec.caption, theme, width=width,
                 top=int(y), margin=margin, max_height=height - int(y) - 220)

    target = Path(out_path)
    ensure_dir(target.parent)
    image.save(target, quality=95)
    return target


def render_intro_card(
    title: str, items: list[str], out_path: str | Path,
    theme: Theme | None = None, size: tuple[int, int] = (1080, 1920),
    subtitle: str = "",
) -> Path:
    """큰 제목 + 항목 미리보기 그리드."""
    theme = theme or Theme()
    width, height = size
    margin = 64
    image, draw = _new_canvas(theme, size)

    y = 230
    font, lines = fit_font(draw, title, width - margin * 2, 420, 118, min_size=64)
    for line in lines:
        line_width = draw.textlength(line, font=font)
        draw.text((width / 2 - line_width / 2, y), line, font=font, fill=theme.text)
        y += font.size * 1.18
    y += 60

    if subtitle:
        sub_font = load_font(44, bold=False)
        y = draw_centered_block(draw, subtitle, sub_font, center_x=width / 2,
                                top=y, max_width=width - margin * 2, fill=theme.muted)
        y += 40

    # 항목 미리보기 — 레퍼런스처럼 순위 카드를 나란히 놓고 미니 그래픽을 넣는다
    preview = _normalize_items(items)[:4]
    if preview:
        gap = 22
        card_w = (width - margin * 2 - gap * (len(preview) - 1)) / len(preview)
        top = y + 70
        # 숏폼 UI 가 가리는 하단을 뺀 나머지를 카드가 채우게 한다
        card_h = max(260.0, min(card_w * 2.0, (height - 300) - top))
        rank_font = load_font(44, bold=True)

        for index, item in enumerate(preview):
            left = margin + index * (card_w + gap)
            is_top = index == len(preview) - 1
            draw.rounded_rectangle(
                [left, top, left + card_w, top + card_h],
                radius=20, fill=theme.surface, outline=theme.border,
                width=6 if is_top else 4,
            )

            rank = f"{len(preview) - index}위"
            rank_width = draw.textlength(rank, font=rank_font)
            draw.text((left + card_w / 2 - rank_width / 2, top + 18), rank,
                      font=rank_font, fill=theme.accent if is_top else theme.text)

            name_font, name_lines = fit_font(
                draw, item["name"], card_w - 20, 110, 34, min_size=20
            )
            ny = top + 84
            for line in name_lines[:3]:
                line_width = draw.textlength(line, font=name_font)
                draw.text((left + card_w / 2 - line_width / 2, ny), line,
                          font=name_font, fill=theme.text)
                ny += name_font.size * 1.18

            # 카드 하단에 미니 그래픽
            mini_top = int(ny + 14)
            mini_box = (int(left + 10), mini_top, int(left + card_w - 10), int(top + card_h - 14))
            if mini_box[3] - mini_box[1] > 60:
                _draw_visual(draw, image, mini_box, item.get("visual", {}), theme)

    target = Path(out_path)
    ensure_dir(target.parent)
    image.save(target, quality=95)
    return target
