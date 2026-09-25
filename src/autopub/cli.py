"""autopub 커맨드라인.

  python -m autopub status              # 오늘 사용량 / 잔여 한도
  python -m autopub trends              # 지금 뜨는 주제 확인
  python -m autopub blog                # 티스토리 1건 발행
  python -m autopub shorts              # 쇼츠 1건 제작 → 유튜브/틱톡 업로드
  python -m autopub shorts --only youtube
  python -m autopub run                 # 블로그 + 쇼츠 한 번씩
  python -m autopub doctor              # 설치/설정 점검
"""
from __future__ import annotations

import argparse
import sys

from .config import load_config
from .logutil import get_logger, setup_logging
from .pipeline import Pipeline
from .state import State

log = get_logger(__name__)


def _pipeline(args) -> Pipeline:
    config = load_config(args.config)
    if args.dry_run:
        config.data.setdefault("general", {})["dry_run"] = True
    state = State(config.path("general.state_file", "state/state.json"))
    return Pipeline(config, state)


def cmd_status(args) -> int:
    pipeline = _pipeline(args)
    print("\n오늘 발행 현황 (KST 기준)")
    print("─" * 62)
    for platform, row in pipeline.status().items():
        if "error" in row:
            print(f"  {platform:<9} ⚠️  {row['error'][:48]}")
            continue
        mark = "✅" if row["ready"] else "⏸️ "
        state = "활성" if row["enabled"] else "비활성"
        print(
            f"  {platform:<9} {mark} {row['used']}/{row['daily_max']}건 "
            f"(남음 {row['remaining']}) [{state}] — {row['reason']}"
        )
    print("─" * 62)
    return 0


def cmd_trends(args) -> int:
    from .trends import TrendAggregator

    pipeline = _pipeline(args)
    topics = TrendAggregator(pipeline.config, pipeline.state).top_topics(limit=args.limit)
    print(f"\n지금 가장 이슈되는 주제 상위 {len(topics)}개")
    print("─" * 62)
    for index, topic in enumerate(topics, 1):
        print(f"{index:2d}. [{topic.score:.2f}] {topic.title}")
        print(f"      소스: {', '.join(topic.sources)} | 근거 {len(topic.evidence)}건"
              + (f" | 검색량 {topic.traffic}" if topic.traffic else ""))
        for evidence in topic.evidence[:2]:
            print(f"      · {evidence.title[:70]}")
    print("─" * 62)
    return 0


def cmd_blog(args) -> int:
    report = _pipeline(args).run_blog()
    print("\n[블로그 결과]")
    print(report.summary())
    return 0 if not report.failed else 1


def cmd_shorts(args) -> int:
    targets = [args.only] if args.only else ["youtube", "tiktok"]
    report = _pipeline(args).run_shorts(targets)
    print("\n[숏폼 결과]")
    print(report.summary())
    return 0 if not report.failed else 1


def cmd_run(args) -> int:
    pipeline = _pipeline(args)
    blog = pipeline.run_blog()
    shorts = pipeline.run_shorts()
    print("\n[전체 결과]")
    print(blog.summary())
    print(shorts.summary())
    return 0 if not (blog.failed or shorts.failed) else 1


def cmd_doctor(args) -> int:
    """실제로 돌리기 전에 빠진 것을 먼저 알려준다."""
    import os
    import shutil

    config = load_config(args.config)
    problems, warnings = [], []

    print("\n환경 점검")
    print("─" * 62)

    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary):
            print(f"  ✅ {binary}")
        else:
            problems.append(f"{binary} 없음 — 영상 제작 불가 (apt install ffmpeg)")
            print(f"  ❌ {binary}")

    for module, label in [
        ("httpx", "HTTP"), ("yaml", "설정"), ("feedparser", "RSS"),
        ("edge_tts", "TTS"), ("markdown", "마크다운"), ("PIL", "카드 렌더링"),
        ("googleapiclient", "YouTube 업로드"), ("playwright", "티스토리 자동화"),
    ]:
        try:
            __import__(module)
            print(f"  ✅ {module} ({label})")
        except ImportError:
            warnings.append(f"{module} 미설치 — {label} 기능 사용 불가")
            print(f"  ⚠️  {module} ({label})")

    # 카드 스타일은 한글 굵은 글꼴이 없으면 글자가 □ 로 나온다
    try:
        from .media.fonts import korean_font_path

        font_path, _ = korean_font_path(bold=True)
        print(f"  ✅ 한글 글꼴: {font_path}")
    except Exception as exc:
        problems.append(f"한글 글꼴 없음 — 카드 자막이 □ 로 나옵니다 ({exc})")
        print("  ❌ 한글 글꼴")

    print("\n환경변수")
    print("─" * 62)
    provider = os.getenv("LLM_PROVIDER", "gemini")
    key_map = {
        "gemini": "GEMINI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openai_compatible": "OPENAI_COMPAT_API_KEY",
    }
    llm_key = key_map.get(provider, "GEMINI_API_KEY")
    if os.getenv(llm_key):
        print(f"  ✅ {llm_key} (LLM_PROVIDER={provider})")
    else:
        problems.append(f"{llm_key} 없음 — 글/대본 생성 불가")
        print(f"  ❌ {llm_key} (LLM_PROVIDER={provider})")

    for name, note in [
        ("PEXELS_API_KEY", "없으면 그라디언트 배경으로 대체"),
        ("PIXABAY_API_KEY", "없으면 그라디언트 배경으로 대체"),
        ("YOUTUBE_API_KEY", "없으면 유튜브 트렌드 수집 생략"),
    ]:
        print(f"  {'✅' if os.getenv(name) else '⚠️ '} {name} — {note}")

    print("\n인증 파일")
    print("─" * 62)
    from pathlib import Path

    for env_name, default, label in [
        ("YOUTUBE_TOKEN_FILE", "secrets/youtube_token.json", "YouTube"),
        ("TIKTOK_TOKEN_FILE", "secrets/tiktok_token.json", "TikTok"),
        ("TISTORY_STORAGE_STATE", "secrets/tistory_state.json", "티스토리 세션"),
    ]:
        path = Path(os.getenv(env_name, default))
        if path.exists():
            print(f"  ✅ {label}: {path}")
        else:
            warnings.append(f"{label} 인증 없음 — {path}")
            print(f"  ⚠️  {label}: {path} (없음)")

    print(f"\n영상 스타일: {config.get('video.style', 'card')} "
          f"(테마 {config.get('video.cards.theme', 'cream')}, "
          f"항목 {config.get('video.cards.item_count', 5)}개)")

    print("\n일일 한도")
    print("─" * 62)
    state = State(config.path("general.state_file", "state/state.json"))
    for platform, row in Pipeline(config, state).status().items():
        if "error" in row:
            print(f"  {platform:<9} ⚠️  {row['error'][:44]}")
        else:
            print(f"  {platform:<9} 하루 최대 {row['daily_max']}건")

    print("\n" + "─" * 62)
    if problems:
        print("치명적 문제:")
        for item in problems:
            print(f"  ❌ {item}")
    if warnings:
        print("확인 필요:")
        for item in warnings:
            print(f"  ⚠️  {item}")
    if not problems:
        print("✅ 발행을 시작할 수 있는 상태입니다.")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="autopub",
        description="트렌드 기반 티스토리/유튜브/틱톡 자동 발행",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-c", "--config", help="설정 파일 경로")
    parser.add_argument("--dry-run", action="store_true", help="실제 발행 없이 결과물만 생성")
    parser.add_argument("-v", "--verbose", action="store_true", help="상세 로그")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="오늘 사용량/잔여 한도").set_defaults(func=cmd_status)
    trends_parser = sub.add_parser("trends", help="지금 뜨는 주제 확인")
    trends_parser.add_argument("-n", "--limit", type=int, default=10)
    trends_parser.set_defaults(func=cmd_trends)
    sub.add_parser("blog", help="티스토리 1건 발행").set_defaults(func=cmd_blog)
    shorts_parser = sub.add_parser("shorts", help="숏폼 1건 제작/업로드")
    shorts_parser.add_argument("--only", choices=["youtube", "tiktok"], help="한 플랫폼만")
    shorts_parser.set_defaults(func=cmd_shorts)
    sub.add_parser("run", help="블로그+숏폼 한 번씩").set_defaults(func=cmd_run)
    sub.add_parser("doctor", help="설치/설정 점검").set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    setup_logging("DEBUG" if args.verbose else "INFO")

    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n중단됨")
        return 130
    except Exception as exc:
        log.error("실행 실패: %s", exc, exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
