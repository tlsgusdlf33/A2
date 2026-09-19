#!/usr/bin/env python3
"""티스토리 로그인 세션 저장 (1회만 실행, 화면이 있는 PC 에서).

티스토리 Open API 가 종료되어 자동화는 로그인된 브라우저 세션에 의존한다.
카카오 로그인은 2단계 인증·캡차가 붙는 경우가 많아, 여기서 사람이 직접
한 번 로그인하고 그 쿠키를 저장해 두는 방식이 가장 안정적이다.

실행:
    python scripts/tistory_login.py

창이 뜨면 카카오 계정으로 로그인하고, 블로그 관리 화면이 보이면
터미널에서 Enter 를 누르세요.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autopub.config import load_config  # noqa: E402


def main() -> int:
    load_config()

    blog = os.getenv("TISTORY_BLOG_NAME", "").strip()
    if not blog:
        print("❌ .env 에 TISTORY_BLOG_NAME 을 먼저 넣으세요 (myblog.tistory.com 의 myblog).")
        return 1

    state_path = Path(os.getenv("TISTORY_STORAGE_STATE", "secrets/tistory_state.json"))

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ playwright 가 필요합니다: pip install playwright && playwright install chromium")
        return 1

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul")
        page = context.new_page()
        page.goto("https://www.tistory.com/auth/login")

        print("\n브라우저에서 카카오 계정으로 로그인하세요.")
        print(f"로그인 후 https://{blog}.tistory.com/manage 가 열리는지 확인하고,")
        input("여기로 돌아와 Enter 를 누르세요… ")

        page.goto(f"https://{blog}.tistory.com/manage")
        page.wait_for_timeout(2000)

        if "manage" not in page.url:
            print(f"⚠️  관리 페이지에 접근하지 못했습니다 (현재: {page.url})")
            print("   로그인이 끝나지 않았을 수 있습니다. 다시 시도하세요.")
            browser.close()
            return 1

        state_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(state_path))
        os.chmod(state_path, 0o600)
        browser.close()

    print(f"\n✅ 세션 저장 완료: {state_path}")
    print("   이제 `python -m autopub blog` 가 자동으로 동작합니다.")
    print("   GitHub Actions 에서 쓰려면 이 파일 내용을 TISTORY_STATE_JSON 시크릿에 넣으세요.")
    print("   ⚠️ 세션은 영구하지 않습니다. 만료되면 이 스크립트를 다시 실행하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
