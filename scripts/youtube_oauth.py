#!/usr/bin/env python3
"""YouTube 업로드용 OAuth 최초 인증 (1회만 실행).

사전 준비:
 1. https://console.cloud.google.com 에서 프로젝트 생성
 2. "YouTube Data API v3" 사용 설정
 3. OAuth 동의 화면 구성 → 테스트 사용자에 본인 계정 추가
 4. 사용자 인증 정보 → OAuth 클라이언트 ID → "데스크톱 앱"
 5. JSON 다운로드 → secrets/youtube_client_secret.json 로 저장

실행:
    python scripts/youtube_oauth.py

브라우저가 없는 서버라면:
    python scripts/youtube_oauth.py --console
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autopub.config import load_config  # noqa: E402

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--console", action="store_true", help="브라우저 없이 URL 을 직접 열어 인증"
    )
    args = parser.parse_args()

    load_config()  # .env 로드

    from google_auth_oauthlib.flow import InstalledAppFlow

    secrets = Path(os.getenv("YOUTUBE_CLIENT_SECRETS", "secrets/youtube_client_secret.json"))
    token_file = Path(os.getenv("YOUTUBE_TOKEN_FILE", "secrets/youtube_token.json"))

    if not secrets.exists():
        print(f"❌ 클라이언트 시크릿 파일이 없습니다: {secrets}")
        print("   Google Cloud 콘솔에서 '데스크톱 앱' OAuth 클라이언트 JSON 을 내려받아 두세요.")
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)

    if args.console:
        # 헤드리스 서버용: 로컬 포트를 열되 URL 을 직접 복사해 인증
        creds = flow.run_local_server(port=0, open_browser=False)
    else:
        creds = flow.run_local_server(port=0)

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(token_file, 0o600)

    print(f"\n✅ 인증 완료 — 토큰 저장: {token_file}")
    print("   GitHub Actions 에서 쓰려면 이 파일 내용을 YOUTUBE_TOKEN_JSON 시크릿에 넣으세요.")
    print(f"\n   cat {token_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
