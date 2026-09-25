"""진행자 영상 + 오버레이 합성.

레퍼런스 채널의 제작 방식을 그대로 따른다. 영상마다 사람을 새로 만들지 않고
**진행자 클립 하나를 재사용**하면서 위에 텍스트와 데이터 카드만 갈아끼운다.
이 방식이라야 무료로 매일 무인 운영이 된다.

진행자 클립이 없으면 AI 앵커 정지 이미지에 느린 확대를 걸어 대체한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..logutil import get_logger
from ..util import ensure_dir, have_binary, run
from .subtitles import Cue, group_cues, write_ass, write_srt
from .tts import WordTiming, probe_duration
from .video import VideoResult, _concat_files, _padded_audio

log = get_logger(__name__)


@dataclass
class PresenterScene:
    overlay_path: Path
    audio_path: Path
    duration: float
    words: list[WordTiming] = field(default_factory=list)


# 정지 이미지 한 장으로 '편집된 느낌'을 내는 샷 변형.
# 레퍼런스 채널은 포즈가 다른 여러 장을 1초 간격으로 컷한다. 무료 엔드포인트로는
# 얼굴을 유지한 포즈 변형이 안 되므로, 같은 사진을 다른 크기로 잡아 컷한다.
#   (시작 줌, 끝 줌, 세로 중심 비율)  — 중심 0.5=가운데, 작을수록 위쪽
# 줌을 크게 주면 안 된다. 진행자 사진이 이미 상반신 컷이라 확대하면
# 머리 위 여백이 사라져 얼굴이 상단 헤드라인 뒤로 밀려 올라간다(실측).
# 원본 구도를 유지하는 선에서 방향만 다르게 줘서 컷 리듬을 만든다.
SHOTS: list[tuple[float, float, float]] = [
    (1.00, 1.05, 0.50),   # 와이드 → 천천히 들어감
    (1.10, 1.04, 0.48),   # 살짝 당긴 채 시작 → 빠짐
    (1.04, 1.11, 0.52),   # 완만하게 들어감
    (1.12, 1.06, 0.46),   # 당긴 채 시작 → 빠짐
]


def _shot_segment(
    still: Path, dest: Path, seconds: float, width: int, height: int, fps: int,
    shot: tuple[float, float, float],
) -> Path:
    """정지 이미지를 지정한 샷(줌·프레이밍)으로 잘라 영상 세그먼트를 만든다."""
    z0, z1, y_center = shot
    frames = max(1, int(round(seconds * fps)))
    step = (z1 - z0) / frames

    # zoompan 은 z 가 단조 증가/감소해야 자연스럽다
    z_expr = (
        f"min(zoom+{abs(step):.6f},{z1:.3f})" if z1 >= z0
        else f"max(zoom-{abs(step):.6f},{z1:.3f})"
    )
    run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-loop", "1", "-i", str(still),
            "-t", f"{seconds:.3f}", "-an",
            "-vf",
            f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
            f"crop={width * 2}:{height * 2},"
            f"zoompan=z='{z_expr}'"
            f":x='iw/2-(iw/zoom/2)'"
            f":y='max(0,min(ih-ih/zoom,ih*{y_center:.2f}-(ih/zoom/2)))'"
            f":d={frames}:s={width}x{height}:fps={fps},setsar=1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(fps),
            str(dest),
        ],
        timeout=600,
    )
    return dest


def _escape_filter_path(path: Path) -> str:
    """ffmpeg 필터 인자에 들어갈 경로 이스케이프."""
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:")


def _global_cues(scenes: list[PresenterScene], hold_seconds: float, max_chars: int) -> list[Cue]:
    """장면별 단어 타이밍을 전체 타임라인 기준으로 합친다.

    장면마다 따로 TTS 를 돌리기 때문에 각 타이밍은 장면 로컬 시각이다.
    누적 오프셋을 더해야 자막이 맞는다.
    """
    cues: list[Cue] = []
    offset = 0.0
    for scene in scenes:
        if scene.words:
            shifted = [
                WordTiming(text=word.text, start=word.start + offset, end=word.end + offset)
                for word in scene.words
            ]
            cues.extend(group_cues(shifted, max_chars=max_chars))
        offset += scene.duration + hold_seconds
    return cues


def build_presenter_video(
    scenes: list[PresenterScene],
    out_path: str | Path,
    work_dir: str | Path,
    *,
    presenter_clip: str | Path | None = None,
    fallback_image: str | Path | None = None,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    hold_seconds: float = 0.35,
    subtitle_opts: dict | None = None,
    bgm_path: str | Path | None = None,
    bgm_volume: float = 0.05,
) -> VideoResult:
    """진행자 영상 위에 장면별 오버레이와 자막을 얹는다."""
    if not have_binary("ffmpeg"):
        raise RuntimeError("ffmpeg 가 필요합니다")
    if not scenes:
        raise ValueError("장면이 하나도 없습니다")

    work = ensure_dir(work_dir)
    target = Path(out_path)
    ensure_dir(target.parent)

    total = sum(scene.duration + hold_seconds for scene in scenes)

    # ---- 오디오 ----
    audio_parts = [
        _padded_audio(scene.audio_path, work / f"seg_{index:02d}.m4a", scene.duration + hold_seconds)
        for index, scene in enumerate(scenes)
    ]
    audio_track = (
        audio_parts[0]
        if len(audio_parts) == 1
        else _concat_files(audio_parts, work / "voice.m4a", work / "a.txt")
    )

    # ---- 자막 ----
    options = dict(subtitle_opts or {})
    cues = _global_cues(scenes, hold_seconds, int(options.get("max_chars_per_cue", 16)))
    ass_path = write_ass(
        cues, work / "subs.ass", width=width, height=height,
        font_name=options.get("font_name", "Noto Sans CJK KR"),
        font_size=int(options.get("font_size", 78)),
        font_color=options.get("font_color", "white"),
        outline_color=options.get("outline_color", "black"),
        outline_width=int(options.get("outline_width", 4)),
        margin_v=int(options.get("margin_v", 820)),
        box=bool(options.get("box", True)),
        box_opacity=float(options.get("box_opacity", 0.55)),
    )
    write_srt(cues, work / "subs.srt")

    # ---- 입력 ----
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if presenter_clip and Path(presenter_clip).exists():
        # 클립이 짧으면 반복해서 전체 길이를 채운다
        clip_seconds = probe_duration(presenter_clip)
        log.info("진행자 클립 사용: %s (%.1f초, 영상 총 %.1f초)",
                 Path(presenter_clip).name, clip_seconds, total)
        cmd += ["-stream_loop", "-1", "-i", str(presenter_clip)]
        base_filter = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},setsar=1[base]"
        )
    elif fallback_image and Path(fallback_image).exists():
        # 장면마다 다른 샷으로 잘라 이어 붙이면 한 장짜리 사진도 편집된 것처럼 보인다
        log.info(
            "진행자 클립이 없어 정지 이미지를 %d개 샷으로 나눠 씁니다: %s",
            len(scenes), Path(fallback_image).name,
        )
        shot_parts = [
            _shot_segment(
                Path(fallback_image), work / f"shot_{index:02d}.mp4",
                scene.duration + hold_seconds, width, height, fps,
                SHOTS[index % len(SHOTS)],
            )
            for index, scene in enumerate(scenes)
        ]
        base_track = (
            shot_parts[0]
            if len(shot_parts) == 1
            else _concat_files(shot_parts, work / "base.mp4", work / "b.txt")
        )
        cmd += ["-i", str(base_track)]
        base_filter = f"[0:v]fps={fps},setsar=1[base]"
    else:
        raise ValueError(
            "진행자 클립도 대체 이미지도 없습니다. "
            "config 의 video.presenter.clip 을 지정하거나 AI 앵커를 준비하세요."
        )

    cmd += ["-i", str(audio_track)]
    audio_input = 1

    bgm_input = None
    if bgm_path and Path(bgm_path).exists():
        cmd += ["-stream_loop", "-1", "-i", str(bgm_path)]
        bgm_input = 2

    overlay_start = (bgm_input or audio_input) + 1
    for scene in scenes:
        cmd += ["-i", str(scene.overlay_path)]

    # ---- 필터 ----
    chains = [base_filter]
    current = "base"
    elapsed = 0.0
    for index, scene in enumerate(scenes):
        span = scene.duration + hold_seconds
        label = f"v{index}"
        chains.append(
            f"[{current}][{overlay_start + index}:v]"
            f"overlay=0:0:enable='between(t,{elapsed:.3f},{elapsed + span:.3f})'[{label}]"
        )
        current = label
        elapsed += span

    chains.append(f"[{current}]ass='{_escape_filter_path(ass_path)}'[vout]")

    if bgm_input is not None:
        chains.append(f"[{bgm_input}:a]volume={bgm_volume}[bgm]")
        chains.append(
            f"[{audio_input}:a][bgm]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_map = f"{audio_input}:a"

    cmd += [
        "-filter_complex", ";".join(chains),
        "-map", "[vout]", "-map", audio_map,
        "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-profile:v", "high", "-level", "4.1", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        str(target),
    ]
    run(cmd, timeout=2400)

    log.info(
        "진행자 영상 완성: %s (장면 %d개, %.1f초, %.1fMB)",
        target.name, len(scenes), total, target.stat().st_size / 1_048_576,
    )
    return VideoResult(
        path=target, duration=total, subtitle_path=ass_path, srt_path=work / "subs.srt"
    )
