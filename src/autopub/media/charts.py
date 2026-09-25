"""카드 안에 들어갈 그래픽 생성 (PIL).

스톡 영상 대신 '직접 그린 도해'를 쓰면 주제와의 연관성이 확실해지고
채널 고유의 톤이 생긴다. 무료이고 외부 API 의존도 없다.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from PIL import ImageDraw

from .fonts import load_font

# 한국 증시 관행: 상승 빨강, 하락 파랑
UP_COLOR = (214, 62, 51)
DOWN_COLOR = (36, 90, 196)


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float

    @property
    def rising(self) -> bool:
        return self.close >= self.open


def synth_candles(pattern: str, count: int = 18, seed: int = 0) -> list[Candle]:
    """패턴 이름에 맞는 캔들 시퀀스를 만들어 낸다.

    실제 시세가 아니라 '설명용 도해'다. 카드 하단 설명과 모양이 일치해야
    시청자가 이해하므로, 패턴별로 마지막 몇 개 봉의 형태를 고정한다.
    """
    rng = random.Random(seed or hash(pattern) & 0xFFFF)
    candles: list[Candle] = []
    price = 100.0

    # 앞부분은 완만한 상승 추세로 공통 처리
    for _ in range(count - 3):
        drift = rng.uniform(-0.8, 1.4)
        open_ = price
        close = max(1.0, price + drift)
        high = max(open_, close) + rng.uniform(0.2, 1.2)
        low = min(open_, close) - rng.uniform(0.2, 1.2)
        candles.append(Candle(open_, high, low, close))
        price = close

    key = pattern.lower()
    if "슈팅" in pattern or "shooting" in key:
        # 위꼬리가 길고 몸통이 작은 봉 → 위에서 매도세가 기다린다
        candles.append(Candle(price, price + 12, price - 1, price + 1.5))
        candles.append(Candle(price + 1.5, price + 2, price - 6, price - 5))
        candles.append(Candle(price - 5, price - 4, price - 9, price - 8))
    elif "장대음봉" in pattern or "bearish" in key:
        candles.append(Candle(price, price + 1, price - 14, price - 13))
        candles.append(Candle(price - 13, price - 12, price - 17, price - 16))
        candles.append(Candle(price - 16, price - 15, price - 19, price - 18))
    elif "도지" in pattern or "doji" in key:
        candles.append(Candle(price, price + 7, price - 7, price + 0.2))
        candles.append(Candle(price, price + 1, price - 6, price - 5))
        candles.append(Candle(price - 5, price - 4, price - 8, price - 7))
    else:
        # 기본: 고점에서 꺾이는 모양
        candles.append(Candle(price, price + 8, price - 1, price + 6))
        candles.append(Candle(price + 6, price + 7, price - 4, price - 3))
        candles.append(Candle(price - 3, price - 2, price - 7, price - 6))

    return candles


def draw_candles(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    candles: list[Candle],
    *,
    highlight_index: int | None = None,
    highlight_label: str = "",
    accent: tuple[int, int, int] = (222, 170, 30),
    text_color: tuple[int, int, int] = (20, 20, 20),
) -> None:
    """캔들차트를 지정한 사각형 안에 그린다."""
    left, top, right, bottom = box
    if not candles:
        return

    pad_x, pad_y = 40, 46
    plot_left, plot_right = left + pad_x, right - pad_x
    plot_top, plot_bottom = top + pad_y, bottom - pad_y

    highest = max(c.high for c in candles)
    lowest = min(c.low for c in candles)
    raw_span = max(highest - lowest, 1e-6)
    # 위쪽에 여유를 둔다. 최고가가 차트 천장에 닿으면 강조 라벨을 올릴 자리가 없다.
    ceiling = highest + raw_span * 0.22
    span = max(ceiling - lowest, 1e-6)

    def y_of(value: float) -> float:
        return plot_bottom - (value - lowest) / span * (plot_bottom - plot_top)

    slot = (plot_right - plot_left) / len(candles)
    body_w = max(6, int(slot * 0.55))

    for index, candle in enumerate(candles):
        cx = plot_left + slot * (index + 0.5)
        color = UP_COLOR if candle.rising else DOWN_COLOR

        # 꼬리
        draw.line(
            [(cx, y_of(candle.high)), (cx, y_of(candle.low))], fill=color, width=max(2, body_w // 6)
        )
        # 몸통 (도지처럼 몸통이 없으면 최소 두께 보장)
        body_top, body_bottom = y_of(max(candle.open, candle.close)), y_of(min(candle.open, candle.close))
        if body_bottom - body_top < 3:
            body_bottom = body_top + 3
        draw.rectangle(
            [cx - body_w / 2, body_top, cx + body_w / 2, body_bottom], fill=color
        )

        if highlight_index is not None and index == highlight_index:
            # 핵심 봉을 노란 테두리로 감싸 시선을 고정
            draw.rounded_rectangle(
                [cx - body_w, y_of(candle.high) - 14, cx + body_w, y_of(candle.low) + 14],
                radius=10, outline=accent, width=5,
            )
            if highlight_label:
                font = load_font(40, bold=True)
                text_w = draw.textlength(highlight_label, font=font)
                label_x = min(cx + body_w + 18, plot_right - text_w - 30)
                # 강조 봉이 차트 꼭대기에 닿으면 라벨이 박스 밖으로 잘린다.
                # 위쪽 공간이 없으면 봉 아래로 내려 붙인다.
                # 강조 박스 바로 위. 위 여유분(22%) 덕분에 보통 여기에 들어간다.
                label_y = y_of(candle.high) - 82
                if label_y < plot_top - 30:
                    # 그래도 자리가 없으면 봉 왼쪽으로 비켜 놓는다 (겹침 방지)
                    label_y = y_of(candle.high) + 10
                    label_x = max(plot_left, cx - body_w - text_w - 32)
                draw.rounded_rectangle(
                    [label_x - 14, label_y - 10, label_x + text_w + 14, label_y + 56],
                    radius=10, outline=DOWN_COLOR, width=4,
                )
                draw.text((label_x, label_y), highlight_label, font=font, fill=DOWN_COLOR)


def draw_bars(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    values: list[float],
    labels: list[str],
    *,
    accent: tuple[int, int, int] = (36, 90, 196),
    text_color: tuple[int, int, int] = (20, 20, 20),
) -> None:
    """가로 막대 그래프. 순위·비교형 주제에 쓴다."""
    left, top, right, bottom = box
    if not values:
        return

    font = load_font(38, bold=True)
    pad = 44
    plot_left, plot_right = left + pad, right - pad
    plot_top, plot_bottom = top + pad, bottom - pad

    largest = max(abs(v) for v in values) or 1.0
    rows = len(values)
    row_h = (plot_bottom - plot_top) / rows
    bar_h = min(row_h * 0.58, 86)
    label_w = max(
        (draw.textlength(label, font=font) for label in labels), default=0
    )
    label_w = min(label_w + 24, (plot_right - plot_left) * 0.4)

    for index, (value, label) in enumerate(zip(values, labels)):
        cy = plot_top + row_h * (index + 0.5)
        draw.text(
            (plot_left, cy - font.size * 0.6), label, font=font, fill=text_color
        )
        bar_left = plot_left + label_w
        width = (plot_right - bar_left) * (abs(value) / largest)
        draw.rounded_rectangle(
            [bar_left, cy - bar_h / 2, bar_left + max(width, 6), cy + bar_h / 2],
            radius=int(bar_h / 2), fill=accent,
        )
        text = f"{value:g}"
        draw.text(
            (bar_left + max(width, 6) + 16, cy - font.size * 0.6),
            text, font=font, fill=text_color,
        )
