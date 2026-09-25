"""TTS 단어 타이밍 → 번인(burn-in) 자막 생성.

별도 STT 없이 edge-tts 의 WordBoundary 를 그대로 쓰기 때문에
싱크가 정확하고 비용이 0이다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..logutil import get_logger
from ..util import ensure_dir
from .tts import WordTiming

log = get_logger(__name__)

# ASS 색상은 &HAABBGGRR (알파-블루-그린-레드) 순서
_COLORS = {
    "white": "&H00FFFFFF",
    "black": "&H00000000",
    "yellow": "&H0000FFFF",
    "red": "&H000000FF",
    "cyan": "&H00FFFF00",
    "green": "&H0000FF00",
}


@dataclass
class Cue:
    text: str
    start: float
    end: float


def _ass_color(name: str) -> str:
    key = str(name).strip().lower()
    if key in _COLORS:
        return _COLORS[key]
    if key.startswith("#") and len(key) == 7:  # #RRGGBB → &H00BBGGRR
        r, g, b = key[1:3], key[3:5], key[5:7]
        return f"&H00{b}{g}{r}".upper()
    return _COLORS["white"]


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


def group_cues(
    words: list[WordTiming],
    *,
    max_chars: int = 18,
    max_gap: float = 0.6,
    min_duration: float = 0.5,
) -> list[Cue]:
    """단어들을 화면 한 줄 분량의 자막 큐로 묶는다.

    - 글자 수가 max_chars 를 넘으면 끊는다
    - 단어 사이 공백(문장 사이 쉼)이 max_gap 이상이면 끊는다
    """
    cues: list[Cue] = []
    buffer: list[WordTiming] = []

    def flush() -> None:
        if not buffer:
            return
        text = " ".join(w.text for w in buffer).strip()
        if text:
            start, end = buffer[0].start, buffer[-1].end
            cues.append(Cue(text=text, start=start, end=max(end, start + min_duration)))
        buffer.clear()

    for word in words:
        if buffer:
            pending = len(" ".join(w.text for w in buffer)) + 1 + len(word.text)
            gap = word.start - buffer[-1].end
            if pending > max_chars or gap > max_gap:
                flush()
        buffer.append(word)
    flush()

    # 겹침 제거 — 앞 큐가 다음 큐 시작을 넘지 않게 한다
    for current, nxt in zip(cues, cues[1:]):
        if current.end > nxt.start:
            current.end = max(current.start + 0.2, nxt.start - 0.02)

    log.info("자막 큐 %d개 생성 (단어 %d개)", len(cues), len(words))
    return cues


def write_ass(
    cues: list[Cue],
    out_path: str | Path,
    *,
    width: int = 1080,
    height: int = 1920,
    font_name: str = "Noto Sans CJK KR",
    font_size: int = 64,
    font_color: str = "white",
    outline_color: str = "black",
    outline_width: int = 4,
    margin_v: int = 420,
    box: bool = False,
    box_opacity: float = 0.55,
) -> Path:
    """libass 로 번인할 .ass 자막 파일을 쓴다.

    box=True 면 글자 뒤에 반투명 띠를 깐다. 야외 촬영본처럼 배경이 복잡한
    영상에서는 테두리만으로는 잘 안 읽혀서 띠가 필요하다.
    """
    target = Path(out_path)
    ensure_dir(target.parent)

    if box:
        # BorderStyle=3 은 글자 뒤 상자. Outline 값이 상자 여백이 된다.
        border_style, border_size = 3, max(6, outline_width * 3)
        alpha = int(max(0.0, min(1.0, 1.0 - box_opacity)) * 255)
        back_colour = f"&H{alpha:02X}000000"
    else:
        border_style, border_size = 1, outline_width
        back_colour = "&H80000000"

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{_ass_color(font_color)},{_ass_color(font_color)},{_ass_color(outline_color)},{back_colour},-1,0,0,0,100,100,0,0,{border_style},{border_size},0,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [header]
    for cue in cues:
        # ASS 에서 중괄호와 줄바꿈은 제어문자라 이스케이프한다
        text = cue.text.replace("{", "(").replace("}", ")").replace("\n", " ")
        lines.append(
            f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Default,,0,0,0,,{text}"
        )

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def write_srt(cues: list[Cue], out_path: str | Path) -> Path:
    """유튜브에 별도 업로드할 수 있는 SRT 도 함께 남긴다."""
    def srt_time(seconds: float) -> str:
        hours, rest = divmod(max(0.0, seconds), 3600)
        minutes, secs = divmod(rest, 60)
        millis = int(round((secs - int(secs)) * 1000))
        return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d},{millis:03d}"

    target = Path(out_path)
    ensure_dir(target.parent)
    blocks = [
        f"{index}\n{srt_time(cue.start)} --> {srt_time(cue.end)}\n{cue.text}\n"
        for index, cue in enumerate(cues, start=1)
    ]
    target.write_text("\n".join(blocks), encoding="utf-8")
    return target
