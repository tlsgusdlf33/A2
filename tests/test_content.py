import pytest

from autopub.content.blog import BlogPost, render_html
from autopub.content.shorts import ShortsScript, Scene, _clean_narration
from autopub.llm.base import LLMError, extract_json
from autopub.media.subtitles import group_cues, write_ass
from autopub.media.tts import WordTiming
from autopub.trends.models import Evidence, Topic


# ----------------------------- LLM 응답 파싱

def test_extract_json_handles_code_fence_and_prose():
    assert extract_json('설명\n```json\n{"a": 1}\n```\n끝') == {"a": 1}
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('앞말 {"a": [1,2]} 뒷말') == {"a": [1, 2]}


def test_extract_json_raises_on_garbage():
    with pytest.raises(LLMError):
        extract_json("JSON 이 전혀 없는 응답")


# ----------------------------- 블로그 HTML

def test_render_html_includes_faq_images_and_sources():
    topic = Topic(
        title="주제",
        evidence=[Evidence(title="기사", url="https://news.example/a", source="연합뉴스")],
    )
    post = BlogPost(
        title="제목", meta_description="요약", tags=["태그"],
        body_markdown="도입부.\n\n## 소제목\n본문.",
        faq=[{"q": "질문?", "a": "답변."}],
        topic=topic, sources_note="메모",
        images=[{"url": "https://img/1.jpg", "alt": "대체", "credit": "Pexels",
                 "credit_url": "https://pexels.com"}],
    )
    html = render_html(post)

    assert "<h2>소제목</h2>" in html
    assert "자주 묻는 질문" in html
    assert "https://img/1.jpg" in html
    assert "https://news.example/a" in html
    # 외부 링크에는 nofollow 를 붙여 스팸 신호를 피한다
    assert 'rel="nofollow noopener"' in html


def test_render_html_escapes_faq_content():
    post = BlogPost(
        title="t", meta_description="", tags=[], body_markdown="본문",
        faq=[{"q": "<script>alert(1)</script>", "a": "답"}],
    )
    assert "<script>" not in render_html(post)


# ----------------------------- 숏폼 대본

def test_clean_narration_strips_tts_noise():
    cleaned = _clean_narration("놀랍죠 🎉 **강조** [출처]")
    # 이모지·마크다운 기호는 TTS 가 소리 내어 읽어버려서 제거해야 한다
    for noise in ("🎉", "*", "[", "]"):
        assert noise not in cleaned
    assert cleaned.endswith(".")


def test_shorts_title_gets_shorts_tag_and_fits_limit():
    script = ShortsScript(title="가" * 200, hook="훅", scenes=[Scene("내레이션")])
    assert script.youtube_title().endswith("#Shorts")
    assert len(script.youtube_title()) <= 100


def test_estimated_seconds_scales_with_text():
    short = ShortsScript(title="t", hook="짧은 훅", scenes=[Scene("한 문장")])
    long = ShortsScript(title="t", hook="짧은 훅", scenes=[Scene("한 문장" * 40)])
    assert long.estimated_seconds > short.estimated_seconds


# ----------------------------- 자막

def _words(pairs):
    return [WordTiming(text=t, start=s, end=e) for t, s, e in pairs]


def test_group_cues_splits_on_length():
    cues = group_cues(_words([
        ("가나다라마바사", 0.0, 0.5),
        ("아자차카타파하", 0.5, 1.0),
        ("한글자", 1.0, 1.4),
    ]), max_chars=18)
    assert len(cues) >= 2


def test_group_cues_splits_on_silence_gap():
    cues = group_cues(_words([("앞", 0.0, 0.4), ("뒤", 3.0, 3.4)]), max_chars=50, max_gap=0.6)
    assert len(cues) == 2


def test_group_cues_removes_overlap():
    cues = group_cues(_words([("가", 0.0, 2.0), ("나", 1.0, 1.5)]), max_chars=1)
    for current, nxt in zip(cues, cues[1:]):
        assert current.end <= nxt.start


def test_write_ass_escapes_braces(tmp_path):
    cues = group_cues(_words([("{중괄호}", 0.0, 1.0)]), max_chars=20)
    path = write_ass(cues, tmp_path / "s.ass")
    content = path.read_text(encoding="utf-8")
    dialogue = [line for line in content.splitlines() if line.startswith("Dialogue")][0]
    # ASS 에서 { } 는 제어 태그라 그대로 두면 자막이 사라진다
    assert "{" not in dialogue.split(",,")[-1]
