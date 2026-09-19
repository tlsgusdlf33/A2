"""무료 스톡 영상/사진 수집 (Pexels, Pixabay).

두 서비스 모두 개인 사용 무료 API 키를 발급한다.
키가 하나도 없거나 검색 결과가 없으면 ffmpeg 로 생성한 그라디언트 배경으로
대체해서 파이프라인이 절대 멈추지 않게 한다.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass
from pathlib import Path

from ..http import client
from ..logutil import get_logger
from ..util import ensure_dir, run

log = get_logger(__name__)


@dataclass
class StockAsset:
    path: Path
    kind: str  # "video" | "image" | "generated"
    credit: str = ""
    credit_url: str = ""
    query: str = ""

    @property
    def is_video(self) -> bool:
        return self.kind == "video"


# ---------------------------------------------------------------- 검색

def _pexels_videos(query: str, per_page: int = 5) -> list[dict]:
    key = os.getenv("PEXELS_API_KEY", "").strip()
    if not key:
        return []
    try:
        with client(timeout=20.0) as http:
            response = http.get(
                "https://api.pexels.com/videos/search",
                params={"query": query, "per_page": per_page, "orientation": "portrait"},
                headers={"Authorization": key},
            )
            response.raise_for_status()
            return response.json().get("videos", [])
    except Exception as exc:
        log.warning("Pexels 영상 검색 실패 (%s): %s", query, exc)
        return []


def _pexels_photos(query: str, per_page: int = 5) -> list[dict]:
    key = os.getenv("PEXELS_API_KEY", "").strip()
    if not key:
        return []
    try:
        with client(timeout=20.0) as http:
            response = http.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "per_page": per_page, "orientation": "landscape"},
                headers={"Authorization": key},
            )
            response.raise_for_status()
            return response.json().get("photos", [])
    except Exception as exc:
        log.warning("Pexels 사진 검색 실패 (%s): %s", query, exc)
        return []


def _pixabay(query: str, kind: str, per_page: int = 5) -> list[dict]:
    key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not key:
        return []
    url = "https://pixabay.com/api/videos/" if kind == "video" else "https://pixabay.com/api/"
    params = {"key": key, "q": query, "per_page": max(3, per_page), "safesearch": "true"}
    if kind != "video":
        params["image_type"] = "photo"
        params["orientation"] = "horizontal"
    try:
        with client(timeout=20.0) as http:
            response = http.get(url, params=params)
            response.raise_for_status()
            return response.json().get("hits", [])
    except Exception as exc:
        log.warning("Pixabay 검색 실패 (%s/%s): %s", kind, query, exc)
        return []


# ---------------------------------------------------------------- 다운로드

def _download(url: str, dest: Path) -> bool:
    try:
        ensure_dir(dest.parent)
        with client(timeout=120.0) as http:
            with http.stream("GET", url) as response:
                response.raise_for_status()
                with open(dest, "wb") as fp:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        fp.write(chunk)
        return dest.stat().st_size > 1024
    except Exception as exc:
        log.warning("다운로드 실패 (%s): %s", url[:80], exc)
        dest.unlink(missing_ok=True)
        return False


def _best_pexels_video_file(video: dict) -> str | None:
    """1080p 에 가장 가까운 파일을 고른다 (너무 큰 4K 는 피한다)."""
    files = [f for f in video.get("video_files", []) if f.get("link")]
    if not files:
        return None
    files.sort(key=lambda f: abs((f.get("height") or 0) - 1920))
    return files[0]["link"]


def _best_pixabay_video_file(hit: dict) -> str | None:
    videos = hit.get("videos", {})
    for quality in ("large", "medium", "small", "tiny"):
        url = videos.get(quality, {}).get("url")
        if url:
            return url
    return None


# ---------------------------------------------------------------- 생성 대체

# 자막을 덧씌운 뒤에도 배경이 보이도록 중간 밝기 이상으로 고른 조합.
# 너무 어두우면 영상이 검은 화면처럼 보이고, 너무 밝으면 흰 자막이 묻힌다.
_GRADIENTS = [
    ("0x1f4e79", "0x4a90d9"),  # 딥블루 → 하늘
    ("0x6a3093", "0xa044ff"),  # 퍼플
    ("0x0b6e4f", "0x2fbf71"),  # 그린
    ("0xb44510", "0xf07b3f"),  # 오렌지
    ("0x243b55", "0x6b7b8c"),  # 차콜블루
    ("0x8e2de2", "0x4a00e0"),  # 바이올렛
]


def generate_gradient(dest: Path, width: int, height: int, seed: int = 0) -> StockAsset:
    """API 키가 없을 때 쓰는 그라디언트 배경 이미지."""
    ensure_dir(dest.parent)
    top, bottom = _GRADIENTS[seed % len(_GRADIENTS)]
    run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi",
            "-i", f"gradients=s={width}x{height}:c0={top}:c1={bottom}:x0=0:y0=0"
                  f":x1={width}:y1={height}:d=1",
            "-frames:v", "1",
            str(dest),
        ],
        timeout=60,
    )
    return StockAsset(path=dest, kind="image", credit="", query="generated gradient")


# ---------------------------------------------------------------- 공개 API

def fetch_backgrounds(
    queries: list[str],
    count: int,
    work_dir: str | Path,
    *,
    providers: list[str] | None = None,
    width: int = 1080,
    height: int = 1920,
) -> list[StockAsset]:
    """배경 소재를 count 개 확보한다. 실패해도 반드시 count 개를 채워서 돌려준다."""
    providers = providers or ["pexels", "pixabay"]
    work = ensure_dir(work_dir)
    assets: list[StockAsset] = []
    queries = queries or ["abstract motion background"]

    for index in range(count):
        query = queries[index % len(queries)]
        asset = _fetch_one(query, work / f"bg_{index:02d}", providers)
        if asset is None:
            asset = generate_gradient(work / f"bg_{index:02d}.png", width, height, seed=index)
            log.info("배경 %d: 스톡 소재를 찾지 못해 그라디언트로 대체 (query=%s)", index, query)
        assets.append(asset)

    videos = sum(1 for a in assets if a.is_video)
    log.info("배경 소재 %d개 확보 (영상 %d / 이미지 %d)", len(assets), videos, len(assets) - videos)
    return assets


def _fetch_one(query: str, dest_stem: Path, providers: list[str]) -> StockAsset | None:
    for provider in providers:
        if provider == "pexels":
            for video in _pexels_videos(query):
                url = _best_pexels_video_file(video)
                dest = dest_stem.with_suffix(".mp4")
                if url and _download(url, dest):
                    return StockAsset(
                        dest, "video",
                        credit=video.get("user", {}).get("name", "Pexels"),
                        credit_url=video.get("url", "https://www.pexels.com"),
                        query=query,
                    )
            for photo in _pexels_photos(query):
                url = photo.get("src", {}).get("large2x") or photo.get("src", {}).get("large")
                dest = dest_stem.with_suffix(".jpg")
                if url and _download(url, dest):
                    return StockAsset(
                        dest, "image",
                        credit=photo.get("photographer", "Pexels"),
                        credit_url=photo.get("url", "https://www.pexels.com"),
                        query=query,
                    )

        elif provider == "pixabay":
            for hit in _pixabay(query, "video"):
                url = _best_pixabay_video_file(hit)
                dest = dest_stem.with_suffix(".mp4")
                if url and _download(url, dest):
                    return StockAsset(
                        dest, "video",
                        credit=hit.get("user", "Pixabay"),
                        credit_url=hit.get("pageURL", "https://pixabay.com"),
                        query=query,
                    )
            for hit in _pixabay(query, "photo"):
                url = hit.get("largeImageURL") or hit.get("webformatURL")
                dest = dest_stem.with_suffix(".jpg")
                if url and _download(url, dest):
                    return StockAsset(
                        dest, "image",
                        credit=hit.get("user", "Pixabay"),
                        credit_url=hit.get("pageURL", "https://pixabay.com"),
                        query=query,
                    )
    return None


def fetch_article_images(queries: list[str], count: int, work_dir: str | Path) -> list[dict]:
    """블로그 본문에 넣을 이미지. 업로드 없이 원본 URL 을 그대로 쓴다.

    티스토리 본문에 외부 이미지를 바로 거는 방식이라 별도 파일 업로드가 필요 없다.
    """
    results: list[dict] = []
    for index in range(count):
        if not queries:
            break
        query = queries[index % len(queries)]
        for photo in _pexels_photos(query, per_page=3):
            url = photo.get("src", {}).get("large")
            if url and all(url != r["url"] for r in results):
                results.append({
                    "url": url,
                    "alt": photo.get("alt") or query,
                    "credit": photo.get("photographer", "Pexels"),
                    "credit_url": photo.get("url", "https://www.pexels.com"),
                })
                break
        else:
            for hit in _pixabay(query, "photo", per_page=3):
                url = hit.get("webformatURL")
                if url and all(url != r["url"] for r in results):
                    results.append({
                        "url": url,
                        "alt": hit.get("tags", query),
                        "credit": hit.get("user", "Pixabay"),
                        "credit_url": hit.get("pageURL", "https://pixabay.com"),
                    })
                    break
    log.info("본문 이미지 %d개 확보", len(results))
    return results
