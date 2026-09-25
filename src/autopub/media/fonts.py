"""한국어 굵은 글꼴 탐색.

숏폼 카드는 굵은 산세리프가 생명이다. Noto Sans CJK 는 .ttc(컬렉션)라서
한국어 자형이 들어 있는 인덱스를 직접 찾아야 한다.
"""
from __future__ import annotations

import functools
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..logutil import get_logger

log = get_logger(__name__)

# 설치 경로 후보 (우분투 / macOS / 수동 설치)
_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-{weight}.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-{weight}.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "assets/fonts/NotoSansKR-{weight}.ttf",
]

def _glyph_pixels(font: ImageFont.FreeTypeFont, char: str) -> bytes:
    """글자 하나를 실제로 그려서 픽셀을 돌려준다."""
    canvas = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(canvas).text((4, 4), char, font=font, fill=255)
    return canvas.tobytes()


def _renders_hangul(path: str, index: int) -> bool:
    """해당 인덱스가 한글 자형을 가지고 있는지 실제로 그려서 확인.

    CJK 글꼴은 한글을 전부 같은 폭(전각)으로 그리기 때문에 폭 비교로는
    판별할 수 없다. 서로 다른 두 글자를 실제로 렌더링해서
    (1) 비어 있지 않고 (2) 서로 다른지를 본다.
    두 조건 중 하나라도 어긋나면 .notdef(빈 사각형)만 찍히는 글꼴이다.
    """
    try:
        font = ImageFont.truetype(path, 40, index=index)
        first, second = _glyph_pixels(font, "한"), _glyph_pixels(font, "글")
        return any(first) and any(second) and first != second
    except Exception:
        return False


def _from_fontconfig(bold: bool) -> str | None:
    """fc-match 로 시스템이 고른 한국어 글꼴 경로를 얻는다."""
    pattern = "sans-serif:lang=ko:weight=" + ("bold" if bold else "regular")
    try:
        out = subprocess.run(
            ["fc-match", "-f", "%{file}", pattern],
            capture_output=True, text=True, timeout=10, check=False,
        )
        path = out.stdout.strip()
        return path if path and Path(path).exists() else None
    except (OSError, subprocess.SubprocessError):
        return None


@functools.lru_cache(maxsize=4)
def korean_font_path(bold: bool = True) -> tuple[str, int]:
    """(글꼴 경로, TTC 인덱스) 를 반환."""
    weight = "Bold" if bold else "Regular"
    searched: list[str] = []

    for template in _CANDIDATES:
        path = template.format(weight=weight)
        searched.append(path)
        if not Path(path).exists():
            continue
        # .ttc 는 여러 언어 자형이 묶여 있어 한국어 인덱스를 찾아야 한다
        limit = 12 if path.endswith(".ttc") else 1
        for index in range(limit):
            if _renders_hangul(path, index):
                log.debug("한국어 글꼴 선택: %s (index=%d)", path, index)
                return path, index

    fallback = _from_fontconfig(bold)
    if fallback:
        for index in range(12 if fallback.endswith(".ttc") else 1):
            if _renders_hangul(fallback, index):
                log.debug("한국어 글꼴 선택(fontconfig): %s (index=%d)", fallback, index)
                return fallback, index

    raise RuntimeError(
        "한국어 글꼴을 찾지 못했습니다. 설치하세요:\n"
        "  Ubuntu: sudo apt install fonts-noto-cjk\n"
        "  macOS : brew install --cask font-noto-sans-cjk-kr\n"
        f"확인한 경로: {', '.join(searched)}"
    )


@functools.lru_cache(maxsize=64)
def load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    path, index = korean_font_path(bold)
    return ImageFont.truetype(path, size, index=index)


def ass_font_name(bold: bool = True) -> str:
    """libass 자막에 넘길 글꼴 패밀리 이름."""
    path, _ = korean_font_path(bold)
    return "Apple SD Gothic Neo" if "AppleSDGothic" in path else "Noto Sans CJK KR"
