# 설치 및 인증 설정

순서대로 따라 하면 됩니다. **티스토리 → 유튜브 → 틱톡** 순으로 난이도가 올라갑니다.

---

## 0. 공통 설치

```bash
git clone https://github.com/tlsgusdlf33/A2.git
cd A2

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

# ffmpeg + 한글 폰트 (자막에 필수)
sudo apt install ffmpeg fonts-noto-cjk        # Ubuntu/Debian
# brew install ffmpeg && brew install --cask font-noto-sans-cjk-kr   # macOS

cp .env.example .env
```

`fc-list :lang=ko` 로 한글 폰트가 잡히는지 확인하세요. 없으면 자막이 **□□□** 로 나옵니다.

---

## 1. LLM 키 (필수)

### 권장: Gemini 무료 티어

1. https://aistudio.google.com/apikey 접속
2. "Create API key" → 키 복사
3. `.env` 에 입력

```ini
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIza...
GEMINI_MODEL=gemini-flash-latest
```

무료 티어의 분당/일일 요청 수는 모델과 시점에 따라 달라집니다.
한도에 걸리면 `config.yaml` 의 `llm.min_interval_sec` 를 늘리세요(기본 5초).

### 대안 1: Groq / OpenRouter 무료 모델

```ini
LLM_PROVIDER=openai_compatible
OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1
OPENAI_COMPAT_API_KEY=gsk_...
OPENAI_COMPAT_MODEL=llama-3.3-70b-versatile
```

### 대안 2: Claude (유료, 품질 최상)

```ini
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-5
```

---

## 2. 스톡 소재 키 (선택, 강력 권장)

없어도 동작하지만 배경이 단색 그라디언트가 됩니다. **영상 품질 차이가 가장 큰 항목**입니다.

- **Pexels**: https://www.pexels.com/api/ → 무료 가입 후 키 발급
- **Pixabay**: https://pixabay.com/api/docs/ → 무료 가입 후 키 발급

```ini
PEXELS_API_KEY=...
PIXABAY_API_KEY=...
```

---

## 2-1. 이미지 생성 (news 스타일)

기본값은 **키가 필요 없습니다.** 그대로 두면 바로 동작합니다.

```ini
# 선택: 다른 공급자로 바꿀 때만
IMAGEGEN_PROVIDER=pollinations     # pollinations | together | cloudflare
TOGETHER_API_KEY=                  # together 사용 시
CF_ACCOUNT_ID=                     # cloudflare 사용 시
CF_API_TOKEN=
```

### AI 앵커 확정 (권장, 1회)

```bash
python -m autopub anchor --candidates 6
# 생성된 파일을 열어보고 마음에 드는 것을 고른 뒤
python -m autopub anchor --pick assets/anchor/candidates/candidate_1137.jpg
```

> ⚠️ 무료 생성 모델은 프롬프트를 느슨하게 주면 선정적이거나 비율이 깨진 결과를
> 내놓습니다. 프롬프트를 강하게 제약해 두었지만, **공개 발행 전에 앵커 이미지를
> 반드시 직접 확인하세요.** 확정해 두면 이후에는 같은 이미지만 재사용합니다.

GitHub Actions 에서 쓰려면 `assets/anchor/anchor.jpg` 를 레포에 커밋하세요
(민감 정보가 아니므로 커밋해도 됩니다).

---

## 3. 티스토리

Open API가 종료되어 **브라우저 세션**으로 동작합니다.

```ini
TISTORY_BLOG_NAME=myblog        # myblog.tistory.com 의 myblog 부분만
TISTORY_STORAGE_STATE=secrets/tistory_state.json
```

### 세션 저장 (화면이 있는 PC에서 1회)

```bash
python scripts/tistory_login.py
```

창이 뜨면 카카오 계정으로 직접 로그인하고, 블로그 관리 화면이 보이면
터미널로 돌아와 Enter를 누르세요. `secrets/tistory_state.json` 에 세션이 저장됩니다.

> **왜 수동인가?** 카카오 로그인은 2단계 인증·캡차가 자주 붙습니다.
> ID/PW 자동 로그인(`TISTORY_KAKAO_ID`/`PW`)도 지원하지만 막힐 확률이 높습니다.

### 안정성을 높이는 설정

티스토리 관리 → **설정 → 편집기 → 기본 에디터를 "HTML"** 로 지정해 두면
모드 전환 단계가 생략되어 자동화가 훨씬 안정적입니다.

### 확인

```bash
python -m autopub blog --dry-run    # out/*.html 로 결과만 확인
python -m autopub blog              # 실제 발행
```

깨지면 `logs/screenshots/` 에 실패 시점 스크린샷이 남습니다.
에디터 UI가 바뀐 경우 `src/autopub/publishers/tistory.py` 상단의 선택자 목록에 새 선택자를 추가하세요.

---

## 4. 유튜브

### Google Cloud 설정

1. https://console.cloud.google.com → 새 프로젝트 생성
2. **API 및 서비스 → 라이브러리** → `YouTube Data API v3` 사용 설정
3. **OAuth 동의 화면** 구성
   - User Type: 외부
   - 테스트 사용자에 **본인 Google 계정을 반드시 추가** (안 하면 인증이 거부됩니다)
4. **사용자 인증 정보 → OAuth 클라이언트 ID → 애플리케이션 유형: 데스크톱 앱**
5. JSON 다운로드 → `secrets/youtube_client_secret.json` 으로 저장

트렌드 수집용 API 키도 하나 만들어 두면 좋습니다(선택, 1 unit만 씁니다).

```ini
YOUTUBE_CLIENT_SECRETS=secrets/youtube_client_secret.json
YOUTUBE_TOKEN_FILE=secrets/youtube_token.json
YOUTUBE_API_KEY=AIza...     # 선택: 유튜브 인기영상 트렌드 수집용
```

### 인증 (1회)

```bash
python scripts/youtube_oauth.py
```

브라우저가 열리고 권한을 허용하면 `secrets/youtube_token.json` 이 생성됩니다.
이후에는 리프레시 토큰으로 자동 갱신됩니다.

### 쿼터 확인

```
Google Cloud 콘솔 → API 및 서비스 → YouTube Data API v3 → 할당량
```

하루 10,000 units가 맞는지 확인하세요. 업로드 1건당 1,600 units가 빠집니다.

> ⚠️ 첫 업로드 후 영상이 **비공개로 잠겨 있지 않은지** 확인하세요.
> API 프로젝트가 컴플라이언스 감사를 통과하기 전에는 잠길 수 있습니다.

---

## 5. 틱톡

가장 까다롭습니다. **심사 전에는 비공개(SELF_ONLY)로만 게시됩니다.**

1. https://developers.tiktok.com → 앱 생성
2. **Products** 에 추가:
   - `Login Kit`
   - `Content Posting API` → 설정에서 **Direct Post 활성화**
3. **Redirect URI** 에 `http://localhost:8080/callback` 등록
4. **Scopes**: `user.info.basic`, `video.publish`, `video.upload`
5. 공개 발행이 필요하면 **Content Posting API 심사 신청** (승인까지 시간이 걸립니다)

```ini
TIKTOK_CLIENT_KEY=aw...
TIKTOK_CLIENT_SECRET=...
TIKTOK_TOKEN_FILE=secrets/tiktok_token.json
```

### 인증 (1회)

```bash
python scripts/tiktok_oauth.py
```

### 공개 범위

`config.yaml` 의 `platforms.tiktok.privacy_level` 은 희망값일 뿐입니다.
실행 시 `creator_info/query` 로 실제 허용된 목록을 조회해서,
쓸 수 없으면 자동으로 `SELF_ONLY` 로 낮추고 경고를 남깁니다.
심사를 통과하면 별도 수정 없이 `PUBLIC_TO_EVERYONE` 이 적용됩니다.

---

## 6. 자동 스케줄링

### 방법 A — GitHub Actions (레포가 공개인 경우 무료)

레포 **Settings → Secrets and variables → Actions** 에 등록합니다.

**Secrets** (민감 정보):

| 이름 | 값 |
|---|---|
| `GEMINI_API_KEY` | Gemini 키 |
| `PEXELS_API_KEY` / `PIXABAY_API_KEY` | 스톡 키 |
| `YOUTUBE_API_KEY` | 트렌드 수집용 (선택) |
| `YOUTUBE_TOKEN_JSON` | `cat secrets/youtube_token.json` 내용 전체 |
| `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` | 틱톡 앱 정보 |
| `TIKTOK_TOKEN_JSON` | `cat secrets/tiktok_token.json` 내용 전체 |
| `TISTORY_STATE_JSON` | `cat secrets/tistory_state.json` 내용 전체 |

**Variables** (민감하지 않은 설정):

| 이름 | 값 |
|---|---|
| `TISTORY_BLOG_NAME` | `myblog` |
| `LLM_PROVIDER` | `gemini` |

발행 횟수를 바꾸려면 `.github/workflows/*.yml` 의 `cron` 시간 개수와
`config.yaml` 의 `daily_max` 를 **함께** 맞추세요. cron은 UTC 기준입니다(KST = UTC+9).

> ⚠️ **비공개 레포라면 Actions 분이 과금됩니다.** 하루 14회 × 5분 ≈ 월 2,100분으로
> 무료 2,000분을 넘깁니다. 공개 레포로 두거나 방법 B를 쓰세요.

### 방법 B — 집 PC / 서버 cron (완전 무료)

```bash
crontab -e
```

```cron
# 블로그: KST 07~21시 2시간 간격 (하루 8회)
0 7,9,11,13,15,17,19,21 * * *  cd /path/to/A2 && .venv/bin/python -m autopub blog   >> logs/cron.log 2>&1

# 숏폼: KST 08,11,14,17,20,23시 (하루 6회)
0 8,11,14,17,20,23 * * *       cd /path/to/A2 && .venv/bin/python -m autopub shorts >> logs/cron.log 2>&1
```

`PYTHONPATH=src` 가 필요하면 명령 앞에 붙이거나 `pip install -e .` 로 설치하세요.

---

## 문제 해결

| 증상 | 원인 / 해결 |
|---|---|
| 자막이 `□□□` 로 나옴 | 한글 폰트 없음 → `sudo apt install fonts-noto-cjk` |
| `ffmpeg 가 필요합니다` | `sudo apt install ffmpeg` |
| 배경이 전부 그라디언트 | Pexels/Pixabay 키 미설정 |
| 티스토리 "제목 입력란을 찾지 못했습니다" | 에디터 UI 변경 → `logs/screenshots/` 확인 후 선택자 추가 |
| 티스토리 로그인 실패 | 세션 만료 → `python scripts/tistory_login.py` 재실행 |
| 유튜브 `quotaExceeded` | 하루 6회 상한 도달 → 다음 날 자동 재개 |
| 틱톡 영상이 비공개로만 올라감 | 앱 심사 미통과 → 개발자 포털에서 심사 신청 |
| 틱톡 토큰 갱신 실패 | 리프레시 토큰 만료 → `python scripts/tiktok_oauth.py` 재실행 |
| `새 주제 없음` | 최근 7일 중복 제외 결과 → `general.dedupe_days` 를 줄이거나 뉴스 피드를 추가 |
| Gemini 429 | 무료 한도 초과 → `llm.min_interval_sec` 증가 또는 다음 날 재시도 |

막히면 먼저 이것부터:

```bash
python -m autopub doctor
```
