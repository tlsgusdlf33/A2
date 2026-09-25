"""뉴스 방송형 카드 — AI 앵커 + 생성 이미지.

두 종류의 화면을 만든다.
  포토 카드 : 내용과 관련된 생성 이미지를 꽉 채우고 하단에 자막 바
  앵커 카드 : 관련 이미지를 만들지 못했을 때 AI 앵커가 전하는 화면

앵커는 '매번 다른 사람'이면 채널로 보이지 않는다. 그래서 한 번 만들어
사람이 확인한 초상을 assets/anchor/ 에 고정해 두고 계속 재사용한다.
이 방식은 품질·일관성 면에서 유리하고, 매 실행마다 새 인물을 뽑아
그대로 올려버리는 사고도 막아 준다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from ..logutil import get_logger
from ..util import ensure_dir
from .cards import Theme, fit_font, paste_cover, wrap_text
from .fonts import load_font
from .imagegen import generate_image

log = get_logger(__name__)

# 앵커 초상 프롬프트.
#
# 어떤 후보를 뽑든 절대 흔들리면 안 되는 제약. 느슨하게 쓰면 선정적이거나
# 비율이 깨진 결과가 나온다(무료 모델에서 실측 확인). 구도·복장·노출을
# 전부 못 박아서 '뉴스 앵커'에서 벗어나지 못하게 한다.
ANCHOR_BASE = (
    "professional Korean female television news anchor, "
    "head and shoulders portrait only, centered, facing camera, "
    "collared blouse buttoned to the neck, modest professional attire, "
    "natural makeup, modern broadcast news studio background with soft bokeh, "
    "even studio key lighting, corporate, photorealistic"
)

# 후보를 시드만 바꿔 뽑으면 비슷한 얼굴만 나온다.
# 나이대·헤어·의상·분위기를 실제로 다르게 줘야 고를 값이 생긴다.
ANCHOR_VARIATIONS: list[dict[str, str]] = [
    {
        "label": "20대 후반 · 단발 보브 · 네이비",
        "prompt": "late 20s, chin-length bob haircut, navy blue blazer, "
                  "bright approachable smile, cool blue studio background",
    },
    {
        "label": "30대 초반 · 긴 생머리 · 버건디",
        "prompt": "early 30s, long straight black hair, burgundy blazer, "
                  "calm composed expression, warm neutral studio background",
    },
    {
        "label": "30대 중반 · 레이어드 단발 · 차콜",
        "prompt": "mid 30s, shoulder-length layered hair, charcoal grey suit jacket, "
                  "authoritative trustworthy expression, dark studio background",
    },
    {
        "label": "20대 중반 · 포니테일 · 아이보리",
        "prompt": "mid 20s, neat ponytail, ivory blazer, "
                  "friendly cheerful expression, bright airy studio background",
    },
    {
        "label": "40대 초반 · 숏컷 · 블랙",
        "prompt": "early 40s, short cropped hair, black suit jacket, "
                  "seasoned dignified expression, deep navy studio background",
    },
    {
        "label": "30대 · 웨이브 · 파스텔 블루",
        "prompt": "early 30s, soft wavy shoulder-length hair, pastel blue blazer, "
                  "gentle warm expression, light grey studio background",
    },
]

# 기본 앵커(변형 없이 쓸 때)
ANCHOR_PROMPT = f"{ANCHOR_BASE}, early 30s, navy business blazer, tidy shoulder-length hair, calm confident neutral expression"


def variation_prompt(index: int) -> tuple[str, str]:
    """(설명 라벨, 프롬프트) — 후보 index 번째."""
    variation = ANCHOR_VARIATIONS[index % len(ANCHOR_VARIATIONS)]
    return variation["label"], f"{ANCHOR_BASE}, {variation['prompt']}"

DEFAULT_ANCHOR_SEED = 4242

# 세로 영상(9:16 = 0.5625)에 가까운 비율로 생성한다.
# 정사각형에 가깝게 뽑으면 cover 크롭에서 얼굴만 확대돼 상반신이 잘린다.
ANCHOR_SIZE = (768, 1344)

# 화면에 항상 띄우는 AI 생성 고지.
# 유튜브/틱톡 모두 합성 콘텐츠 표시를 요구하고, API 플래그와 별개로
# 화면에도 보이는 편이 시청자 신뢰에 낫다.
AI_BADGE_TEXT = "AI 생성 영상"

NEWS_RED = (198, 40, 40)
NEWS_NAVY = (18, 32, 58)
WHITE = (255, 255, 255)
SCRIM = (8, 12, 20)


@dataclass
class BroadcastSpec:
    headline: str
    caption: str = ""
    kicker: str = "속보"
    ticker: str = ""
    image_path: Path | None = None


# --------------------------------------------------------------------------
# 앵커 초상
# --------------------------------------------------------------------------

def anchor_portrait(
    assets_dir: str | Path = "assets/anchor",
    *,
    seed: int = DEFAULT_ANCHOR_SEED,
    size: tuple[int, int] = ANCHOR_SIZE,
    provider: str | None = None,
    auto_create: bool = True,
) -> Path | None:
    """확정된 앵커 초상 경로. 없으면(허용 시) 한 장 만들어 둔다."""
    folder = ensure_dir(assets_dir)
    approved = folder / "anchor.jpg"
    if approved.exists() and approved.stat().st_size > 2048:
        return approved

    if not auto_create:
        return None

    log.info("확정된 앵커 초상이 없어 seed=%d 로 새로 만듭니다", seed)
    result = generate_image(
        ANCHOR_PROMPT, approved, size=size, seed=seed,
        provider=provider, cache_dir=folder / ".cache",
    )
    if result is None:
        return None

    log.warning(
        "앵커 초상을 자동 생성했습니다: %s — 공개 발행 전에 반드시 눈으로 확인하세요. "
        "다른 얼굴을 원하면 `python -m autopub anchor --candidates 6` 로 후보를 뽑으세요.",
        approved,
    )
    return approved


def generate_anchor_candidates(
    out_dir: str | Path,
    count: int = 6,
    *,
    start_seed: int = 1000,
    size: tuple[int, int] = ANCHOR_SIZE,
    provider: str | None = None,
) -> list[tuple[Path, str]]:
    """후보 초상을 여러 장 만든다. 사람이 고르는 용도.

    나이대·헤어·의상·분위기를 바꿔 가며 뽑기 때문에 취향의 폭이 넓다.
    반환값은 (파일 경로, 설명 라벨) 목록.
    """
    folder = ensure_dir(out_dir)
    made: list[tuple[Path, str]] = []
    for index in range(count):
        label, prompt = variation_prompt(index)
        seed = start_seed + index * 137
        log.info("후보 %d/%d 생성 중: %s", index + 1, count, label)
        result = generate_image(
            prompt, folder / f"candidate_{index + 1:02d}_{seed}.jpg",
            size=size, seed=seed, provider=provider,
        )
        if result:
            made.append((result.path, label))
    return made


# --------------------------------------------------------------------------
# 레이아웃 조각
# --------------------------------------------------------------------------

def _bottom_scrim(image: Image.Image, top: int, strength: int = 215) -> None:
    """사진 아래쪽을 어둡게 깔아 흰 글씨가 읽히게 한다."""
    width, height = image.size
    depth = height - top
    if depth <= 0:
        return
    overlay = Image.new("RGBA", (width, depth), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for offset in range(depth):
        alpha = int(strength * (offset / depth) ** 1.3)
        draw.line([(0, offset), (width, offset)], fill=(*SCRIM, alpha))
    image.paste(
        Image.alpha_composite(
            image.crop((0, top, width, height)).convert("RGBA"), overlay
        ).convert("RGB"),
        (0, top),
    )


def _ai_badge(draw, width: int, top: int = 48) -> None:
    """AI 생성 고지 배지."""
    font = load_font(30, bold=True)
    text_width = draw.textlength(AI_BADGE_TEXT, font=font)
    right = width - 48
    left = right - text_width - 36
    draw.rounded_rectangle(
        [left, top, right, top + 52], radius=26,
        fill=(0, 0, 0), outline=(255, 255, 255), width=2,
    )
    draw.text((left + 18, top + 9), AI_BADGE_TEXT, font=font, fill=WHITE)


def _lower_third(
    image: Image.Image, draw, *, width: int, top: int,
    kicker: str, headline: str, accent: tuple[int, int, int] = NEWS_RED,
) -> int:
    """방송 하단 자막 바. 반환값은 블록이 끝나는 y."""
    margin = 48
    y = top

    if kicker:
        kicker_font = load_font(38, bold=True)
        kicker_width = draw.textlength(kicker, font=kicker_font)
        draw.rectangle([margin, y, margin + kicker_width + 44, y + 62], fill=accent)
        draw.text((margin + 22, y + 10), kicker, font=kicker_font, fill=WHITE)
        y += 62

    headline_font, lines = fit_font(
        draw, headline, width - margin * 2 - 44, 260, 72, min_size=44
    )
    block_height = len(lines) * headline_font.size * 1.22 + 36
    draw.rectangle([margin, y, width - margin, y + block_height], fill=NEWS_NAVY)
    # 왼쪽 강조 줄
    draw.rectangle([margin, y, margin + 10, y + block_height], fill=accent)

    text_y = y + 18
    for line in lines:
        draw.text((margin + 32, text_y), line, font=headline_font, fill=WHITE)
        text_y += headline_font.size * 1.22

    return int(y + block_height)


def _caption_strip(draw, *, width: int, top: int, text: str, max_height: int) -> int:
    if not text.strip():
        return top
    margin = 48
    font, lines = fit_font(draw, text, width - margin * 2 - 56, max_height - 40, 46, min_size=32)
    height = len(lines) * font.size * 1.3 + 40
    draw.rounded_rectangle(
        [margin, top + 14, width - margin, top + 14 + height],
        radius=16, fill=(255, 255, 255, 255), outline=NEWS_NAVY, width=3,
    )
    y = top + 34
    for line in lines:
        line_width = draw.textlength(line, font=font)
        draw.text((width / 2 - line_width / 2, y), line, font=font, fill=(20, 20, 20))
        y += font.size * 1.3
    return int(top + 14 + height)


def _ticker(draw, *, width: int, height: int, text: str) -> None:
    if not text.strip():
        return
    bar_height = 64
    top = height - 150
    draw.rectangle([0, top, width, top + bar_height], fill=NEWS_NAVY)
    draw.rectangle([0, top, 150, top + bar_height], fill=NEWS_RED)

    label_font = load_font(30, bold=True)
    draw.text((30, top + 16), "NEWS", font=label_font, fill=WHITE)

    font = load_font(32, bold=False)
    clipped = wrap_text(draw, text, font, width - 200)[0]
    draw.text((176, top + 16), clipped, font=font, fill=WHITE)


# --------------------------------------------------------------------------
# 카드
# --------------------------------------------------------------------------

def render_broadcast_card(
    spec: BroadcastSpec,
    out_path: str | Path,
    *,
    size: tuple[int, int] = (1080, 1920),
    accent: tuple[int, int, int] = NEWS_RED,
    blur_background: bool = False,
) -> Path:
    """사진(또는 앵커) 위에 방송 자막을 올린 카드."""
    width, height = size
    image = Image.new("RGB", size, NEWS_NAVY)

    if spec.image_path and Path(spec.image_path).exists():
        # 사진은 화면 전체를 덮고, 아래쪽은 스크림으로 눌러 글씨를 살린다
        paste_cover(image, Path(spec.image_path), (0, 0, width, height), radius=0)
        if blur_background:
            image = image.filter(ImageFilter.GaussianBlur(2))

    _bottom_scrim(image, top=int(height * 0.44))
    draw = ImageDraw.Draw(image)

    _ai_badge(draw, width)

    # 하단 자막 블록을 아래에서부터 쌓아 올린다
    caption_font_probe = load_font(46, bold=True)
    caption_lines = (
        len(wrap_text(draw, spec.caption, caption_font_probe, width - 152))
        if spec.caption else 0
    )
    caption_height = caption_lines * caption_font_probe.size * 1.3 + 54 if caption_lines else 0

    headline_top = int(height - 230 - caption_height - 300)
    end = _lower_third(
        image, draw, width=width, top=headline_top,
        kicker=spec.kicker, headline=spec.headline, accent=accent,
    )
    _caption_strip(draw, width=width, top=end, text=spec.caption, max_height=caption_height + 40)
    _ticker(draw, width=width, height=height, text=spec.ticker)

    target = Path(out_path)
    ensure_dir(target.parent)
    image.save(target, quality=95)
    return target
