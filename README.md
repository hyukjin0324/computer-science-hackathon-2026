# Bento 🍱 — 팀플을 대신 챙겨 주는 AI

> 2026 컴퓨터학부 해커톤 출품작

대회 공고를 읽고, 계획을 짜고, 늦어지면 먼저 알려 주는 **팀 프로젝트 에이전트**예요.
대회 찾기부터 제출 점검까지 한 대화창에서 이어지고, GitHub 저장소를 지켜보다가 막힌 곳을 급한 순서로 찾아 **사람이 승인한 일만** 대신 처리해요.

| 문서 | 링크 |
|---|---|
| 배포 / 데모 | (제출 전에 채우기) |
| 기능명세서 (Notion) | https://app.notion.com/p/41a74ed5ffce816c9174e84558855a89 |
| 프로토타입 (화면 시안) | https://claude.ai/artifact/XYafR8CsNqyoVyFXfvB8Jr |
| 발표 자료 | (제출 전에 채우기) |

## 왜 만들었나

팀플이 망가지는 이유는 실력보다 **챙기는 일**이에요. 마감을 놓치고, 리뷰가 며칠씩 멈추고, 맡은 사람이 조용해지고, 제출 직전에 README·영상이 빠진 걸 알게 돼요.
Bento는 이런 일을 대신 지켜보고, 해야 할 일을 **누가, 언제까지** 할지 정해서 먼저 알려 줘요.

## 무엇을 하나요

| 단계 | Bento가 하는 일 |
|---|---|
| 대회 찾기 | 학부 공지·개발 행사 모음에서 해커톤·공모전을 모아 보여 줘요 |
| 신청 | 공고에서 마감·팀 인원·참가 자격·제출물을 뽑고, 원문 인용과 대조해요. 신청 버튼은 사용자가 직접 눌러요 |
| 계획 | 제출 마감에서 거꾸로 단계별 계획을 짜고 GitHub 마일스톤·할 일·담당자로 만들어요. 발표 자료·시연 시나리오 같은 중간 마감도 넣어요 |
| 협업 | 저장소를 지켜보다가 막힌 곳을 찾아 내 할 일 / 팀장이 정할 일 / 팀원 할 일로 나눠 보여 줘요 |
| 진행 점검 | 계획과 실제 진행을 비교해서 늦은 단계를 찾고, 남은 시간에 맞게 다시 짜요 |
| 제출 | 제출물이 다 있는지, 저장소가 공개인지, 마감 뒤 커밋이 없는지 점검해요 |

### 저장소에서 찾아 주는 일 (규칙 기반, AI 비용 없음)

리뷰 대기 · 수정 요청 무응답 · 리뷰 없이 머지 · 담당 없는 할 일 · 맡기만 하고 손 안 댄 할 일 · 연락이 끊긴 팀원 · 초대 미수락 · main 빌드 깨짐 · 비밀키 커밋(값은 절대 보여 주지 않음) · 큰 파일·설치 폴더 · 계정에 안 잡힌 커밋 · 마감 임박·마감 뒤 커밋 · 공고 마감 날짜 변경(하루 한 번 다시 읽기)

### 에이전트 원칙

```text
관찰(저장소·공고 읽기) → 판단(규칙으로 우선순위) → 제안(서명된 토큰) → 사람이 고치고 승인 → 실행 직전 다시 확인 → 실행 → 결과 링크
```

- 승인받고 직접 하는 일: 리뷰어 지정, 댓글, 이슈·마일스톤 만들기, 담당자 지정, 이슈 닫기, 팀 메시지
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

## 구조

```text
public/index.html   모바일 웹 화면 (홈 · 대화 · 캘린더 · 나)
app/main.py         API (/api/...)
app/github.py       GitHub App 클라이언트 (설치 토큰, 읽기 위주)
app/state.py        저장소 상태 (리뷰 대기, CI, 충돌 등)
app/signals.py      PR·이슈 밖의 신호 (main 빌드, 활동, 파일, 초대, 키 모양)
app/triage.py       막힌 곳 감지 규칙 → 우선순위와 행동 제안
app/watch.py        팀원·저장소 상태 감지 (조용한 팀원, 비밀키, 마감 뒤 커밋 …)
app/ownership.py    누구 몫인지 정하기 (역할·팀장·일의 양)
app/actions.py      할 수 있는 행동 목록 · 검증 · 실행
app/proposals.py    제안 서명 (DB 없이 승인 흐름을 안전하게)
app/notice.py       공고 분석 (Claude) + 원문 인용 대조
app/rules.py        AI 없이 공고 읽기 (규칙)
app/schedule.py     마감에서 거꾸로 계획 짜기
app/discover.py     대회 찾기 (사이트별 10초 간격, 목록은 1시간에 한 번)
app/chat.py         대화창 자유 대화 (Claude)
app/auth.py         GitHub 로그인
app/demo.py         GitHub App 없이 돌아가는 데모 저장소
api/index.py        Vercel 진입점
```

기술: Python 3.11 · FastAPI · httpx · Anthropic SDK · 바닐라 JS (빌드 없음) · GitHub App · Vercel

## 로컬 실행

```bash
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt uvicorn pytest
.venv/bin/python -m pytest -q
.venv/bin/python -m uvicorn app.main:app --port 8787
```

http://localhost:8787 을 열면 데모 저장소로 동작해요. GitHub App이나 AI 키 없이도 모든 화면을 볼 수 있어요.

## 실제 GitHub 연결 (GitHub App 만들기)

1. GitHub → Settings → Developer settings → GitHub Apps → New GitHub App
2. Webhook URL: `https://<배포 주소>/api/github/webhook`, Webhook secret: 무작위 문자열
3. Repository permissions
   - Contents: **Read-only** (머지·푸시 권한을 주지 않기 위해 쓰기 금지)
   - Pull requests: Read and write
   - Issues: Read and write
   - Checks: Read-only
   - Metadata: Read-only
4. Subscribe to events: Pull request, Pull request review, Issues, Issue comment, Check run
5. 만든 뒤 Private key를 내려받고, 팀 저장소에 앱을 설치
6. 환경 변수 설정 (`.env.example` 참고): `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `BENTO_REPO`, `BENTO_SIGNING_KEY`, `BENTO_ACCESS_CODE`, `ANTHROPIC_API_KEY`

배포 주소가 공개라면 `BENTO_ACCESS_CODE`를 꼭 설정하세요. 설정하지 않으면 주소를 아는 누구나 승인 버튼을 누를 수 있어요.

## 배포 (Vercel)

```bash
npx vercel deploy --prod
```

환경 변수는 Vercel 프로젝트 Settings → Environment Variables에 넣어요. `.env`와 `*.pem`은 저장소와 배포에 올리지 않아요.

## 알려진 한계

- 데모 저장소는 서버 메모리에 있어서, 서버가 다시 시작되면 처음 상태로 돌아가요.
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
