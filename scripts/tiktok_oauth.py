#!/usr/bin/env python3
"""TikTok Content Posting API 최초 인증 (1회만 실행).

사전 준비:
 1. https://developers.tiktok.com 에서 앱 생성
 2. Products 에 "Login Kit" 과 "Content Posting API" 추가
 3. Content Posting API 설정에서 "Direct Post" 활성화
 4. Redirect URI 에 http://localhost:8080/callback 등록
 5. Scopes: user.info.basic, video.publish, video.upload
 6. .env 에 TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET 입력

⚠️ 앱 심사(audit) 전에는 SELF_ONLY(비공개)로만 게시됩니다.
   공개 발행을 하려면 개발자 포털에서 심사를 신청해 통과해야 합니다.

실행:
    python scripts/tiktok_oauth.py
"""
from __future__ import annotations

import http.server
import json
import os
import secrets as secrets_mod
import socketserver
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autopub.config import load_config  # noqa: E402
from autopub.http import client  # noqa: E402

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REDIRECT_URI = os.getenv("TIKTOK_REDIRECT_URI", "http://localhost:8080/callback")
SCOPES = "user.info.basic,video.publish,video.upload"

_received: dict = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        _received.update({k: v[0] for k, v in params.items()})

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in _received
        message = "인증이 완료되었습니다. 터미널로 돌아가세요." if ok else "인증에 실패했습니다."
        self.wfile.write(f"<html><body><h2>{message}</h2></body></html>".encode("utf-8"))

    def log_message(self, *args):  # 조용히
        pass


def main() -> int:
    load_config()

    client_key = os.getenv("TIKTOK_CLIENT_KEY", "").strip()
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
    if not (client_key and client_secret):
        print("❌ .env 에 TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET 를 먼저 넣으세요.")
        return 1

    state = secrets_mod.token_urlsafe(16)
    params = {
        "client_key": client_key,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    port = urllib.parse.urlparse(REDIRECT_URI).port or 8080
    server = socketserver.TCPServer(("", port), Handler)
    server.allow_reuse_address = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(f"\n브라우저에서 아래 주소를 열어 권한을 허용하세요:\n\n{url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    deadline = time.time() + 300
    while "code" not in _received and time.time() < deadline:
        time.sleep(1)
    server.shutdown()

    if "code" not in _received:
        print("❌ 인증 코드를 받지 못했습니다 (시간 초과).")
        return 1
    if _received.get("state") != state:
        print("❌ state 불일치 — 인증을 중단합니다.")
        return 1

    with client(timeout=30.0) as http_client:
        response = http_client.post(
            TOKEN_URL,
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "code": _received["code"],
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code >= 400:
        print(f"❌ 토큰 교환 실패 ({response.status_code}): {response.text[:400]}")
        return 1

    tokens = response.json()
    if "access_token" not in tokens:
        print(f"❌ 예상치 못한 응답: {json.dumps(tokens, ensure_ascii=False)[:400]}")
        return 1

    tokens["obtained_at"] = int(time.time())
    token_file = Path(os.getenv("TIKTOK_TOKEN_FILE", "secrets/tiktok_token.json"))
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(token_file, 0o600)

    print(f"\n✅ 인증 완료 — 토큰 저장: {token_file}")
    print("   GitHub Actions 에서 쓰려면 이 파일 내용을 TIKTOK_TOKEN_JSON 시크릿에 넣으세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
