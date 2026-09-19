"""edge-tts 기반 음성 합성 (무료, Microsoft Edge Neural 보이스).

단어 경계(WordBoundary) 이벤트를 함께 받아오기 때문에
자막 타이밍을 별도 STT 없이 정확하게 맞출 수 있다.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import ssl
from dataclasses import dataclass
from pathlib import Path

from ..logutil import get_logger
from ..util import ensure_dir, have_binary, run

log = get_logger(__name__)


@dataclass
class WordTiming:
    text: str
    start: float  # 초
    end: float


@dataclass
class TTSResult:
    audio_path: Path
    words: list[WordTiming]
    duration: float


def _apply_custom_ca() -> None:
    """사내/에이전트 프록시 환경 대응.

    edge-tts 는 certifi 로 만든 SSL 컨텍스트를 내부에 고정해 두기 때문에
    커스텀 CA 를 쓰는 환경에서는 웹소켓 핸드셰이크가 실패한다.
    CA 번들 환경변수가 있을 때만 컨텍스트를 교체한다.
    """
    bundle = next(
        (
            os.environ[var]
            for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
            if os.environ.get(var) and os.path.exists(os.environ[var])
        ),
        None,
    )
    if not bundle:
        return
    try:
        from edge_tts import communicate as _communicate

        _communicate._SSL_CTX = ssl.create_default_context(cafile=bundle)
        log.debug("edge-tts SSL 컨텍스트를 %s 로 교체했습니다", bundle)
    except (ImportError, AttributeError):  # 버전이 바뀌면 조용히 무시
        pass


async def _synthesize_async(
    text: str, voice: str, rate: str, pitch: str, out_path: Path
) -> list[WordTiming]:
    import edge_tts

    kwargs = {"rate": rate, "pitch": pitch}
    # edge-tts 7.x 의 기본값은 SentenceBoundary 라서 단어 단위 타이밍이 나오지 않는다.
    # 자막을 정확히 끊으려면 반드시 WordBoundary 를 요청해야 한다.
    if "boundary" in inspect.signature(edge_tts.Communicate.__init__).parameters:
        kwargs["boundary"] = "WordBoundary"

    communicate = edge_tts.Communicate(text, voice, **kwargs)
    words: list[WordTiming] = []

    with open(out_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # offset/duration 단위는 100나노초
                start = chunk["offset"] / 10_000_000
                end = start + chunk["duration"] / 10_000_000
                words.append(WordTiming(text=chunk["text"], start=start, end=end))

    return words


def probe_duration(path: str | Path) -> float:
    """ffprobe 로 미디어 길이(초)를 구한다."""
    if not have_binary("ffprobe"):
        raise RuntimeError("ffprobe 가 필요합니다 (ffmpeg 설치 필요)")
    proc = run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=60,
    )
    return float(proc.stdout.strip())


def synthesize(
    text: str,
    out_path: str | Path,
    *,
    voice: str = "ko-KR-SunHiNeural",
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> TTSResult:
    """텍스트를 mp3 로 합성하고 단어 타이밍을 반환."""
    if not text.strip():
        raise ValueError("합성할 텍스트가 비어 있습니다")

    target = Path(out_path)
    ensure_dir(target.parent)
    _apply_custom_ca()

    words = asyncio.run(_synthesize_async(text, voice, rate, pitch, target))

    if target.stat().st_size == 0:
        raise RuntimeError(
            f"TTS 결과가 비어 있습니다. 보이스 이름({voice})이 올바른지 확인하세요."
        )

    duration = probe_duration(target)
    log.info(
        "TTS 완료: %s (%.1f초, 단어 타이밍 %d개, 보이스=%s)",
        target.name, duration, len(words), voice,
    )
    return TTSResult(audio_path=target, words=words, duration=duration)


def list_korean_voices() -> list[str]:  # pragma: no cover - 진단용
    """사용 가능한 한국어 보이스 목록."""
    import edge_tts

    _apply_custom_ca()

    async def _run():
        voices = await edge_tts.list_voices()
        return [v["ShortName"] for v in voices if v["Locale"].startswith("ko")]

    return asyncio.run(_run())
