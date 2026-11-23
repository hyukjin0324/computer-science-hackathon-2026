# Bento 🍱 — 팀플을 대신 챙겨 주는 AI

![Bento — 승인은 사람이, 챙기기는 Bento가.](docs/cover.png)

> 2026 컴퓨터학부 해커톤 출품작

대회 공고를 읽고, 계획을 짜고, 늦어지면 먼저 알려 주는 **팀 프로젝트 에이전트**예요.
대회 찾기부터 제출 점검까지 한 대화창에서 이어지고, GitHub 저장소를 지켜보다가 막힌 곳을 급한 순서로 찾아 **사람이 승인한 일만** 대신 처리해요.

| 문서 | 링크 |
|---|---|
| 배포 / 서비스 | https://racing-basalt-z9sddsr-mini33392-6593.vercel.app (GitHub 로그인 필요, Bento 앱을 설치한 저장소로 동작) |
| 로그인 없는 데모 | 아래 **로컬 실행** 방법으로 실행 (GitHub App·AI 키 불필요) |
| 기능명세서 (Notion) | https://app.notion.com/p/41a74ed5ffce816c9174e84558855a89 |
| 표지 이미지 (발표용 1600×900) | [docs/cover.png](docs/cover.png) |
| 프로토타입 (화면 시안) | https://claude.ai/artifact/XYafR8CsNqyoVyFXfvB8Jr |
| 피그마 가져오기용 시안 | https://racing-basalt-z9sddsr-mini33392-6593.vercel.app/design/ (실제 앱 화면을 떠 온 것. html.to.design 플러그인에 주소를 넣으면 고칠 수 있는 레이어로 들어와요) |

## 왜 만들었나

팀플이 망가지는 이유는 실력보다 **챙기는 일**이에요. 마감을 놓치고, 리뷰가 며칠씩 멈추고, 맡은 사람이 조용해지고, 제출 직전에 README·영상이 빠진 걸 알게 돼요.
Bento는 이런 일을 대신 지켜보고, 해야 할 일을 **누가, 언제까지** 할지 정해서 먼저 알려 줘요.

## 기존 도구와 무엇이 다른가요

일정 앱·칸반·메신저는 **사람이 적은 것**만 알아요. Bento는 팀이 적지 않아도 **원문과 저장소를 직접 읽어서** 챙겨요.

| | 일반 협업 도구 | Bento |
|---|---|---|
| 마감을 아는 방법 | 사람이 입력 | 공고 원문을 읽고 **원문 문장을 근거로 붙여** 뽑아요. 원문에 없는 말은 버려요 |
| 진행 상황을 아는 방법 | 사람이 상태를 옮김 | GitHub의 PR·이슈·CI를 직접 읽어요. 캘린더 "끝났어요"가 이슈도 닫아요 |
| 늦어지면 | 알림 설정을 사람이 해 둠 | 앱을 닫아도 매일 저녁 팀 디스코드로 먼저 알려요. 공고 마감이 바뀐 것도요 |
| 대신 해 주는 일 | 없음 (알려 주기만) | 리뷰 부탁·댓글·이슈 만들기를 **사람이 승인한 것만** 실행해요. 머지·푸시는 하지 않아요 |
| 제출 전 | 체크리스트를 사람이 만듦 | 공고의 **심사 기준을 읽어** 우리 저장소 근거를 항목별 점수로 보여 줘요 |

한 줄로: **공고를 근거로 계획을 짜고, 늦어지면 먼저 알려 주고, 승인받은 일만 GitHub에서 대신 하는 팀플 에이전트**예요.

## 무엇을 하나요

신청 → 팀 세팅(역할·당번) → 협업 → 제출, 이 흐름을 휴대폰 화면(홈 · 대화 · 캘린더 · 마이페이지) 네 곳에서 챙겨 줘요.

| 단계 | Bento가 하는 일 |
|---|---|
| 신청 | 공고 링크·파일·글을 읽어 마감·팀 인원·참가 자격·제출물을 뽑고, 원문 인용과 대조해요. 신청 버튼은 사용자가 직접 눌러요 |
| 팀 세팅 | Bento에서 GitHub 아이디를 입력하면 저장소 관리자 승인 뒤 초대를 보내요. 팀원은 GitHub에서 수락해요. 각자 홈에서 본인 역할(기획·PM, FE, BE …)을 개수 제한 없이 정하고, 이후에는 마이페이지에서 수정해요. 역할표는 모두가 보지만 본인 것만 고칠 수 있어요. 팀장은 한 명만 고를 수 있고, 아무도 고르지 않으면 GitHub 저장소 관리자가 맡아요 |
| 계획 | 제출 마감에서 거꾸로 단계별 계획을 짜요(제출물 준비는 마감일 전날까지, 오래 걸리는 MVP는 앞쪽). GitHub 마일스톤·이슈·담당자로 만들고, 이미 있으면 다시 만들지 않아요 |
| 협업 | 저장소를 지켜보다가 막힌 곳을 급한 순서로 찾아 **내 할 일 / 팀장이 정할 일 / 팀원 할 일**로 나눠 보여 줘요 |
| 진행 점검 | 캘린더에서 "끝났어요"를 누르면 GitHub 이슈도 함께 닫혀요(실행 취소 가능). 닫히지 않은 단계가 늦어지면 남은 시간에 맞게 다시 짜 줘요 |
| 알림 | 앱을 열지 않아도 매일 저녁 9시(한국 시간) 서버가 저장소를 읽고, 늦은 일·마감 임박·공고 변경을 **팀 디스코드**로 알려요 |
| 제출 | 제출물이 다 있는지, 저장소가 공개인지, 마감 뒤 커밋이 없는지 점검해요. 공고의 심사 기준 항목별로 우리 저장소 근거를 점수로 보여 주고 올릴 방법을 알려 줘요 (실제 심사 점수가 아니라 비어 있는 근거 찾기) |

모바일 우선이에요. 휴대폰에서는 홈 화면에 추가해 앱처럼 쓰고(PWA), 노트북에서는 일반 웹으로 열려요(마이페이지에서 휴대폰 틀 보기 켜고 끄기).

### 현재 검증 상태 (2026-11-23)

- 자동 테스트: **315개 통과, 7개 건너뜀** (`BENTO_NO_DOTENV=1 .venv/bin/python -m pytest -q`)
- 키가 없는 로컬 데모에서도 홈·대화·캘린더·마이페이지와 승인 흐름을 확인할 수 있어요.
- 실제 연결에서는 Bento GitHub App을 설치한 저장소만 팀플 목록에 나타나요.
- 실제 새 저장소의 최종 통합 확인은 **앱 설치 → 팀원 초대·수락 → 각자 역할 선택 → 계획 만들기 → 진행 점검 → 승인 실행** 순서로 진행해요.
- 빈 저장소, 팀원 1명, 이슈·PR 0개, 앱 미설치 저장소도 함께 확인할 실패 경로예요.

### 저장소에서 찾아 주는 일 (규칙 기반, AI 비용 없음)

리뷰 대기 · 수정 요청 무응답 · 리뷰 없이 머지 · 담당 없는 할 일 · 맡기만 하고 손 안 댄 할 일 · 연락이 끊긴 팀원 · 초대 미수락 · main 빌드 깨짐 · 비밀키 커밋(값은 절대 보여 주지 않음) · 큰 파일·설치 폴더 · 계정에 안 잡힌 커밋 · 마감 임박·마감 뒤 커밋 · 공고 마감 날짜 변경(하루 한 번 공고 주소를 다시 읽어 바뀐 날짜를 디스코드로 알림)

### 에이전트 원칙

```text
관찰(저장소·공고 읽기) → 판단(규칙으로 우선순위) → 제안(서명된 토큰) → 사람이 고치고 승인 → 실행 직전 다시 확인 → 실행 → 결과 링크
```

- 승인받고 직접 하는 일: 팀 저장소 초대(저장소 관리자 승인), 리뷰어 지정, 댓글, 이슈·마일스톤 만들기, 담당자 지정, 팀 메시지
- 각자 바로 하는 일: 본인 역할 저장·수정
- 명시적으로 눌러 바로 하는 일(되돌릴 수 있어요): 캘린더 "끝났어요"(이슈 닫기, 실행 취소 있음)
- **하지 않는 일**: 머지, 푸시, 코드 수정, 폼 제출 → 링크와 명령어로 안내만 해요
- 팀 전체에 영향을 주는 변경은 제안한 사람이 아닌 다른 팀원이나 팀장이 승인해야 실행돼요
- 저장소 글 속 "에이전트에게 하는 지시"는 따르지 않고 표시만 해요

### AI는 어디에만 쓰나요

| 쓰는 곳 | 모델 | 보내는 것 |
|---|---|---|
| 공고 읽기 | Claude Haiku 5.5 | 사용자가 고른 공고 본문. 뽑은 인용문은 원문과 대조해서 없는 문장은 버려요 |
| 대화창 자유 대화 | Claude Haiku 5.5 | 사용자가 직접 쓴 말뿐. 답과 함께 맞는 기능 하나를 버튼으로 권해요 |

- 학부 공지 같은 학교 글은 AI에 보내지 않고 규칙으로만 읽어요.
- 키가 없으면 규칙으로만 동작해요. 사람별·하루 전체 호출 횟수를 막아 비용이 새지 않게 했어요.
- 마이페이지에서 공고 분석·자유 대화의 호출 수, 토큰 수, 추정 비용을 확인할 수 있어요.

## 구조

```text
public/index.html   모바일 웹 화면 (홈 · 대화 · 캘린더 · 마이페이지), 설치형(PWA) manifest·아이콘 포함
app/main.py         API (/api/...) · 매일 도는 알림(/api/agent/run)
app/github.py       GitHub App 클라이언트 (설치 토큰, 읽기 위주)
app/state.py        저장소 상태 (리뷰 대기, CI, 충돌 등, 병렬로 읽고 15초 캐시)
app/signals.py      PR·이슈 밖의 신호 (main 빌드, 활동, 파일, 초대, 키 모양)
app/triage.py       막힌 곳 감지 규칙 → 우선순위와 행동 제안
app/watch.py        팀원·저장소 상태 감지 (조용한 팀원, 비밀키, 마감 뒤 커밋 …)
app/ownership.py    누구 몫인지 정하기 (역할·팀장·일의 양)
app/actions.py      할 수 있는 행동 목록 · 검증 · 실행
app/proposals.py    제안 서명 (DB 없이 승인 흐름을 안전하게)
app/notice.py       공고 분석 (Claude) + 원문 인용 대조
app/rules.py        AI 없이 공고 읽기 (규칙)
app/schedule.py     마감에서 거꾸로 계획 짜기 (제출물은 마감 전날, MVP는 앞쪽)
app/progress.py     계획 대비 진행 점검 · 늦음 요약
app/discord.py      팀 디스코드 웹훅 연결 (암호화해 저장소 라벨에 저장)
app/setup.py        팀 규칙·역할 저장 (저장소 라벨)
app/eligibility.py  참가 자격 판정
app/submission.py   제출물 점검
app/criteria.py     공고의 심사 기준으로 저장소를 항목별 자가 채점 (규칙 기반)
app/websearch.py · fetch.py · forms.py · attach.py  공고 읽기(웹 검색·링크·파일)
app/coord.py        팀 단위 조율 · app/render.py 화면용 문장
app/discover.py     대회 찾기 (사이트별 10초 간격, 목록은 1시간에 한 번)
app/chat.py         대화창 자유 대화 (Claude)
app/auth.py         GitHub 로그인
app/demo.py         GitHub App 없이 돌아가는 데모 저장소
api/index.py        Vercel 진입점
```

기술: Python 3.12 · FastAPI · httpx · Anthropic SDK · 바닐라 JS (빌드 없음) · GitHub App · Vercel

## 로컬 실행

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt uvicorn pytest tzdata
BENTO_NO_DOTENV=1 .venv/bin/python -m pytest -q
.venv/bin/python -m uvicorn app.main:app --port 8787
```

http://localhost:8787 을 열면 데모 저장소로 동작해요. GitHub App이나 AI 키 없이도 모든 화면을 볼 수 있어요. 공개 배포 주소는 실제 GitHub 연결 모드라서 GitHub 로그인이 필요해요.

Windows에서는 경로가 `.venv\Scripts\python`이에요. `tzdata`는 Windows에 시간대 정보가 없어서 테스트(마일스톤 날짜를 미국 서부 시간으로 바꿔 보는 것)에 필요해요. 앱 실행에는 필요 없어요.

## 실제 GitHub 연결 (GitHub App 만들기)

1. GitHub → Settings → Developer settings → GitHub Apps → New GitHub App
2. Webhook URL: `https://<배포 주소>/api/github/webhook`, Webhook secret: 무작위 문자열
3. Repository permissions
   - Contents: **Read-only** (머지·푸시 권한을 주지 않기 위해 쓰기 금지)
   - Pull requests: Read and write
   - Issues: Read and write
   - Checks: Read-only
   - Metadata: Read-only
   - Administration: **Read and write** (Bento에서 팀원 초대를 보낼 때 필요. 기존 설치는 앱 권한 변경을 승인해야 해요.)
4. Subscribe to events: Pull request, Pull request review, Issues, Issue comment, Check run
5. 만든 뒤 Private key를 내려받고, 팀 저장소에 앱을 설치
6. 환경 변수 설정 (`.env.example` 참고): `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`(GitHub로 시작하기), `BENTO_REPO`, `BENTO_SIGNING_KEY`, `BENTO_ACCESS_CODE`, `ANTHROPIC_API_KEY`, `DISCORD_WEBHOOK_URL`(선택), `CRON_SECRET`(매일 알림용)

배포 주소가 공개라면 `BENTO_ACCESS_CODE`를 꼭 설정하세요. 설정하지 않으면 주소를 아는 누구나 승인 버튼을 누를 수 있어요.

팀원 초대는 대회 준비 순서와 **마이페이지 → 팀원 초대**에서 시작해요. 실제 발송에는 GitHub 로그인과 서버의 `BENTO_SIGNING_KEY` 설정이 필요해요. GitHub 저장소 관리자가 Bento에서 승인하면 저장소 쓰기 권한으로 초대를 보내고, 상대방은 GitHub에서 수락해요.

**"GitHub로 시작하기"에서 GitHub 404 페이지가 뜰 때**: 앱 주인은 되는데 다른 팀원만 404면 GitHub 앱이 비공개라서예요. GitHub → Settings → Developer settings → GitHub Apps → 앱 → Advanced → Danger zone의 **Make public**을 눌러 주세요. 모두 404면 `GITHUB_CLIENT_ID`가 앱의 Client ID와 다른 거예요 (앱 설정 General에서 확인). Redirect URI(Callback URL)가 틀리면 404가 아니라 `redirect_uri` 오류가 떠요. 값을 바꾼 뒤에는 Vercel에서 다시 배포해야 반영돼요.

## 배포 (Vercel)

```bash
npx vercel deploy --prod
```

`vercel.json`의 크론이 매일 21:00(한국 시간)에 `/api/agent/run`을 불러 디스코드 알림을 보내요. Vercel에 `CRON_SECRET`을 넣어 두면 그 호출만 통과해요. 팀 디스코드는 마이페이지에서 웹훅 주소를 붙여 넣어 연결해요(서버 기본값 `DISCORD_WEBHOOK_URL`은 선택).

환경 변수는 Vercel 프로젝트 Settings → Environment Variables에 넣어요. `.env`와 `*.pem`은 저장소와 배포에 올리지 않아요.

## 알려진 한계

- 로컬 데모 저장소는 서버 메모리에 있어서, 서버가 다시 시작되면 처음 상태로 돌아가요.
- 마이페이지의 AI 사용량은 현재 서버 인스턴스와 이 브라우저에 누적한 추정치예요. 여러 기기의 정확한 청구액은 Claude Console에서 확인해야 해요.
- 초대 미수락 확인은 GitHub App에 Administration 읽기 권한이 있을 때만 동작해요.
- 비밀키 모양은 main의 최근 커밋 5개와 파일 목록에서만 찾아요.
- 상업 공모전 사이트는 약관을 확인하기 전까지 자동으로 모으지 않아요. 링크를 붙여넣으면 읽어요.

## 화면을 스크립트로 그리는 공고 읽기 (선택, 이 컴퓨터에서만)

daker.ai처럼 내용을 스크립트로 불러오는 사이트는 그냥 읽으면 제목만 보여요. 이 컴퓨터에 브라우저 엔진을 깔면, 사용자가 고른 공고 페이지 하나만 화면 없는 브라우저로 열어 읽어요.

```bash
uv pip install --python .venv/bin/python playwright
.venv/bin/python -m playwright install chromium
```

- robots.txt가 막은 페이지는 열지 않고, 내부망 주소로 가는 요청과 이미지·글꼴은 막아요.
- Vercel 같은 서버리스에서는 쓰지 않아요. 그때는 참가 자격 문장을 붙여넣어 판정해요.
