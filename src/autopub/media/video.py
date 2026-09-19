"""ffmpeg 기반 세로형(9:16) 숏폼 영상 합성.

단계는 셋으로 나눠 놓았다. 한 번에 거대한 filter_complex 를 만들면
실패했을 때 원인을 찾기가 어렵기 때문이다.
  1) 배경 소재를 규격(1080x1920)에 맞춘 세그먼트로 정규화
  2) concat 디먹서로 이어붙여 오디오 길이만큼 배경 트랙 생성
  3) 어둡게 + 자막 번인 + 오디오 먹싱으로 최종 인코딩
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from ..logutil import get_logger
from ..util import ensure_dir, have_binary, run
from .stock import StockAsset
from .subtitles import Cue, write_ass, write_srt
from .tts import TTSResult

log = get_logger(__name__)


@dataclass
class VideoResult:
    path: Path
    duration: float
    subtitle_path: Path | None = None
    srt_path: Path | None = None
    thumbnail_path: Path | None = None


def _require_ffmpeg() -> None:
    missing = [b for b in ("ffmpeg", "ffprobe") if not have_binary(b)]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} 가 필요합니다. "
            "Ubuntu: sudo apt install ffmpeg / macOS: brew install ffmpeg"
        )


def _normalize_segment(
    asset: StockAsset,
    dest: Path,
    seconds: float,
    width: int,
    height: int,
    fps: int,
) -> Path:
    """배경 하나를 지정 길이/해상도의 무음 mp4 세그먼트로 만든다."""
    cover = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps={fps}"
    )

    if asset.is_video:
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            # 소재가 짧으면 반복 재생해서 길이를 채운다
            "-stream_loop", "-1", "-i", str(asset.path),
            "-t", f"{seconds:.3f}",
            "-an",
            "-vf", cover,
        ]
    else:
        # 정지 이미지는 느린 줌(켄 번스)으로 움직임을 준다
        frames = max(1, int(round(seconds * fps)))
        zoom = (
            f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
            f"crop={width * 2}:{height * 2},"
            f"zoompan=z='min(zoom+0.0009,1.25)'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps={fps},setsar=1"
        )
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-loop", "1", "-i", str(asset.path),
            "-t", f"{seconds:.3f}",
            "-an",
            "-vf", zoom,
        ]

    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        str(dest),
    ]
    run(cmd, timeout=600)
    return dest


def _concat_segments(segments: list[Path], dest: Path, work: Path) -> Path:
    """concat 디먹서로 세그먼트를 이어붙인다 (재인코딩 없음)."""
    list_file = work / "concat.txt"
    list_file.write_text(
        "\n".join(f"file '{segment.resolve()}'" for segment in segments) + "\n",
        encoding="utf-8",
    )
    run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy",
            str(dest),
        ],
        timeout=600,
    )
    return dest


def build_short_video(
    tts_result: TTSResult,
    cues: list[Cue],
    backgrounds: list[StockAsset],
    out_path: str | Path,
    work_dir: str | Path,
    *,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    clip_seconds: float = 5.0,
    darken: float = 0.35,
    subtitle_opts: dict | None = None,
    bgm_path: str | Path | None = None,
    bgm_volume: float = 0.08,
) -> VideoResult:
    """내레이션 오디오 + 배경 + 자막 → 세로형 mp4."""
    _require_ffmpeg()

    work = ensure_dir(work_dir)
    target = Path(out_path)
    ensure_dir(target.parent)

    duration = tts_result.duration
    if duration <= 0:
        raise ValueError("오디오 길이가 0입니다")
    # 마지막 자막이 잘리지 않도록 꼬리를 조금 남긴다
    total = duration + 0.6

    # ---- 1) 배경 세그먼트 ----
    if not backgrounds:
        raise ValueError("배경 소재가 하나도 없습니다")

    needed = max(1, math.ceil(total / clip_seconds))
    segments: list[Path] = []
    for index in range(needed):
        asset = backgrounds[index % len(backgrounds)]
        remaining = total - index * clip_seconds
        length = min(clip_seconds, remaining)
        if length <= 0.1:
            break
        segment = work / f"seg_{index:02d}.mp4"
        _normalize_segment(asset, segment, length, width, height, fps)
        segments.append(segment)

    log.info("배경 세그먼트 %d개 생성 (총 %.1f초)", len(segments), total)

    # ---- 2) 이어붙이기 ----
    background_track = (
        segments[0] if len(segments) == 1 else _concat_segments(segments, work / "bg.mp4", work)
    )

    # ---- 3) 자막 + 오디오 ----
    options = dict(subtitle_opts or {})
    ass_path = write_ass(
        cues,
        work / "subs.ass",
        width=width,
        height=height,
        font_name=options.get("font_name", "Noto Sans CJK KR"),
        font_size=int(options.get("font_size", 64)),
        font_color=options.get("font_color", "white"),
        outline_color=options.get("outline_color", "black"),
        outline_width=int(options.get("outline_width", 4)),
        margin_v=int(options.get("margin_v", 420)),
    )
    srt_path = write_srt(cues, work / "subs.srt")

    # libass 필터에 넘기는 경로는 콜론/백슬래시를 이스케이프해야 한다
    ass_arg = str(ass_path.resolve()).replace("\\", "/").replace(":", r"\:")

    video_filters = [f"eq=brightness=-{darken:.2f}"] if darken > 0 else []
    if options.get("enabled", True):
        video_filters.append(f"ass='{ass_arg}'")
    video_filters.append("format=yuv420p")
    video_chain = f"[0:v]{','.join(video_filters)}[v]"

    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(background_track), "-i", str(tts_result.audio_path)]

    use_bgm = bool(bgm_path) and Path(bgm_path).exists()
    if use_bgm:
        cmd += ["-stream_loop", "-1", "-i", str(bgm_path)]
        audio_chain = (
            f"[2:a]volume={bgm_volume}[bgm];"
            f"[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=0[a]"
        )
        filter_complex = f"{video_chain};{audio_chain}"
        audio_map = "[a]"
    else:
        filter_complex = video_chain
        audio_map = "1:a"

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", audio_map,
        "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-profile:v", "high", "-level", "4.1",
        "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        # 스트리밍 시작이 빨라지도록 moov atom 을 앞으로
        "-movflags", "+faststart",
        str(target),
    ]
    run(cmd, timeout=1800)

    size_mb = target.stat().st_size / 1_048_576
    log.info("영상 완성: %s (%.1f초, %.1fMB)", target.name, total, size_mb)

    return VideoResult(
        path=target, duration=total, subtitle_path=ass_path, srt_path=srt_path
    )


def extract_thumbnail(video_path: str | Path, dest: str | Path, at_seconds: float = 1.0) -> Path:
    """영상에서 썸네일 한 장을 뽑는다."""
    _require_ffmpeg()
    target = Path(dest)
    ensure_dir(target.parent)
    run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{at_seconds:.2f}", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "2",
            str(target),
        ],
        timeout=120,
    )
    return target
