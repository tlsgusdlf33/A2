"""무료 이미지 생성 (text-to-image).

진짜 text-to-video 는 무료로 돌릴 방법이 없다. 대신 이미지를 생성해서
느린 확대/이동으로 움직임을 주는 방식을 쓴다. 뉴스 숏폼에서는
이 편이 오히려 자막 가독성이 좋고 실패 지점도 적다.

공급자는 교체 가능하며 기본값은 키가 필요 없는 Pollinations 이다.
"""
from __future__ import annotations

import hashlib
import os
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..http import client
from ..logutil import get_logger
from ..util import ensure_dir

log = get_logger(__name__)

# 생성 서비스가 이미지 하단에 워터마크를 넣는 경우가 있어 잘라낸다
WATERMARK_CROP_RATIO = 0.06

# 어떤 프롬프트에도 항상 붙이는 품질/안전 제약
QUALITY_SUFFIX = (
    "photorealistic, sharp focus, professional photography, "
    "natural proportions, well composed"
)
NEGATIVE = (
    "nsfw, nude, lingerie, cleavage, suggestive pose, deformed, extra limbs, "
    "extra fingers, mutated hands, watermark, text overlay, logo, blurry, lowres"
)


@dataclass
class GeneratedImage:
    path: Path
    prompt: str
    seed: int
    provider: str
    cached: bool = False


def _cache_key(provider: str, prompt: str, seed: int, size: tuple[int, int]) -> str:
    raw = f"{provider}|{prompt}|{seed}|{size[0]}x{size[1]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _postprocess(path: Path, crop_watermark: bool) -> None:
    """워터마크 영역을 잘라내고 RGB 로 정규화한다."""
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            if crop_watermark and WATERMARK_CROP_RATIO > 0:
                height = rgb.height
                rgb = rgb.crop((0, 0, rgb.width, int(height * (1 - WATERMARK_CROP_RATIO))))
            rgb.save(path, "JPEG", quality=92)
    except OSError as exc:
        log.warning("생성 이미지 후처리 실패 (%s): %s", path, exc)


# --------------------------------------------------------------------------
# 공급자
# --------------------------------------------------------------------------

def _pollinations(prompt: str, seed: int, size: tuple[int, int], dest: Path) -> bool:
    """키가 필요 없는 무료 엔드포인트.

    ⚠️ 익명 호출로 실제 서빙되는 모델은 하나뿐이다(현재 sana).
    model 파라미터에 다른 이름을 넣어도 조용히 무시된다.
    image-to-image(kontext)는 계정 등록이 필요해 여기선 쓸 수 없다.
    """
    model = os.getenv("IMAGEGEN_MODEL", "sana")
    encoded = urllib.parse.quote(prompt, safe="")
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={size[0]}&height={size[1]}&seed={seed}&nologo=true&model={model}"
    )
    with client(timeout=180.0) as http:
        response = http.get(url)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}")
        if not response.content or len(response.content) < 2048:
            raise RuntimeError("응답이 너무 작습니다 (이미지가 아님)")
        dest.write_bytes(response.content)
    return True


def _together(prompt: str, seed: int, size: tuple[int, int], dest: Path) -> bool:
    """Together AI 무료 티어 (FLUX.1-schnell). TOGETHER_API_KEY 필요."""
    import base64

    key = os.getenv("TOGETHER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("TOGETHER_API_KEY 없음")

    payload = {
        "model": os.getenv("TOGETHER_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell-Free"),
        "prompt": prompt,
        "width": size[0],
        "height": size[1],
        "steps": 4,
        "n": 1,
        "seed": seed,
        "response_format": "b64_json",
    }
    with client(timeout=180.0) as http:
        response = http.post(
            "https://api.together.xyz/v1/images/generations",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()

    items = data.get("data") or []
    if not items:
        raise RuntimeError("이미지가 반환되지 않았습니다")
    dest.write_bytes(base64.b64decode(items[0]["b64_json"]))
    return True


def _cloudflare(prompt: str, seed: int, size: tuple[int, int], dest: Path) -> bool:
    """Cloudflare Workers AI 무료 티어. 계정 ID 와 토큰 필요."""
    account = os.getenv("CF_ACCOUNT_ID", "").strip()
    token = os.getenv("CF_API_TOKEN", "").strip()
    if not (account and token):
        raise RuntimeError("CF_ACCOUNT_ID / CF_API_TOKEN 없음")

    model = os.getenv("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
    with client(timeout=180.0) as http:
        response = http.post(
            f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}",
            json={"prompt": prompt, "seed": seed},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")

        # 모델에 따라 raw 이미지 또는 base64 JSON 을 돌려준다
        if response.headers.get("content-type", "").startswith("image/"):
            dest.write_bytes(response.content)
            return True

        import base64

        payload = response.json().get("result", {})
        encoded = payload.get("image")
        if not encoded:
            raise RuntimeError("응답에 image 가 없습니다")
        dest.write_bytes(base64.b64decode(encoded))
    return True


_PROVIDERS = {
    "pollinations": _pollinations,
    "together": _together,
    "cloudflare": _cloudflare,
}


# --------------------------------------------------------------------------
# 공개 API
# --------------------------------------------------------------------------

def generate_image(
    prompt: str,
    dest: str | Path,
    *,
    size: tuple[int, int] = (896, 1152),
    seed: int = 0,
    provider: str | None = None,
    cache_dir: str | Path | None = None,
    retries: int = 3,
    crop_watermark: bool = True,
    add_quality_suffix: bool = True,
) -> GeneratedImage | None:
    """이미지를 생성한다. 실패하면 None 을 돌려주고 호출부가 대체 경로를 택한다."""
    name = (provider or os.getenv("IMAGEGEN_PROVIDER") or "pollinations").strip().lower()
    handler = _PROVIDERS.get(name)
    if handler is None:
        log.warning("알 수 없는 이미지 공급자: %s", name)
        return None

    full_prompt = f"{prompt}. {QUALITY_SUFFIX}" if add_quality_suffix else prompt
    target = Path(dest)
    ensure_dir(target.parent)

    # 같은 프롬프트/시드는 다시 만들지 않는다 (무료 티어 아끼기 + 앵커 일관성)
    cache_path: Path | None = None
    if cache_dir:
        cache_path = ensure_dir(cache_dir) / f"{_cache_key(name, full_prompt, seed, size)}.jpg"
        if cache_path.exists() and cache_path.stat().st_size > 2048:
            import shutil

            shutil.copyfile(cache_path, target)
            log.info("이미지 캐시 사용: %s", target.name)
            return GeneratedImage(target, full_prompt, seed, name, cached=True)

    for attempt in range(1, retries + 1):
        try:
            handler(full_prompt, seed, size, target)
            _postprocess(target, crop_watermark)
            if target.stat().st_size < 2048:
                raise RuntimeError("생성 결과가 비어 있습니다")
            if cache_path:
                import shutil

                shutil.copyfile(target, cache_path)
            log.info("이미지 생성 완료 [%s] seed=%d: %s", name, seed, target.name)
            return GeneratedImage(target, full_prompt, seed, name)
        except Exception as exc:
            wait = min(30, 2**attempt * 2)
            log.warning(
                "이미지 생성 실패 [%s] (%d/%d): %s — %ds 후 재시도",
                name, attempt, retries, exc, wait,
            )
            target.unlink(missing_ok=True)
            if attempt < retries:
                time.sleep(wait)

    log.warning("이미지 생성을 포기합니다 (프롬프트: %.60s)", prompt)
    return None


def scene_prompt(subject: str) -> str:
    """장면 이미지 프롬프트를 안전하게 감싼다.

    뉴스 주제에는 실존 인물 이름이 섞여 있다. 그 사람을 그려내면
    '실제로 일어나지 않은 장면'을 뉴스처럼 보여주는 셈이라
    사람은 아예 그리지 않도록 못을 박는다.
    """
    return (
        f"{subject}, editorial stock photograph, no people, no faces, "
        "no text, no logos, cinematic lighting, vertical composition"
    )
