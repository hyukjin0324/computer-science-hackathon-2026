# Bento

대회 공고를 넣으면 마감·제출물을 깃허브 일정으로 바꾸고, 개발 팀플에서 막힌 곳을 급한 순서로 찾아 **승인받은 뒤 대신 처리하는** 팀플 에이전트.

- 승인받고 직접 하는 일: 리뷰어 지정, 댓글, 이슈·마일스톤 만들기, 담당자 지정, 이슈 닫기, 디스코드 팀 메시지
- 하지 않는 일: 머지, 푸시, 코드 수정, 폼 제출 → 링크와 명령어로 안내
- 저장소 글 속 "에이전트에게 하는 지시"는 따르지 않고 표시만 함

## 구조

```text
public/index.html   모바일 웹 화면 (대화 · 캘린더 · 보관함 · 나)
app/main.py         API (/api/...)
app/github.py       GitHub App 클라이언트 (설치 토큰)
app/state.py        저장소 상태 만들기 (리뷰 대기, CI, 충돌 등)
app/triage.py       막힌 곳 감지 규칙 → 우선순위와 행동 제안
app/actions.py      할 수 있는 행동 목록 · 검증 · 실행
app/proposals.py    제안 서명 (DB 없이 승인 흐름을 안전하게)
app/notice.py       공고 분석 (Claude) + 원문 인용 대조 + 일정 계획
app/demo.py         GitHub App 없이 돌아가는 가짜 저장소
api/index.py        Vercel 진입점
```

흐름: 감지(규칙) → 제안(서명된 토큰) → 사람이 글 수정·승인 → **실행 직전 저장소 다시 읽고 검증** → 실행 → 결과 링크.

## 로컬 실행

```bash
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt uvicorn pytest
.venv/bin/python -m pytest -q
.venv/bin/python -m uvicorn app.main:app --port 8787
```

http://localhost:8787 을 열면 데모 저장소로 동작해요.

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

환경 변수는 Vercel 프로젝트 Settings → Environment Variables에 넣어요.

## 알려진 한계

- 로그인이 없어서 "누가 승인했는지"는 기록하지 않아요. 팀 접근 코드로만 막아요.
- 웹훅은 서명 확인까지만 하고, 알림 전송은 아직 없어요. 화면을 열 때 저장소를 새로 읽어요.
- 데모 저장소는 서버 메모리에 있어서, 서버가 다시 시작되면 처음 상태로 돌아가요.

## 화면을 스크립트로 그리는 공고 읽기 (선택, 이 컴퓨터에서만)

daker.ai처럼 내용을 스크립트로 불러오는 사이트는 그냥 읽으면 제목만 보여요. 이 컴퓨터에 브라우저 엔진을 깔면, 사용자가 고른 공고 페이지 하나만 화면 없는 브라우저로 열어 읽어요.

```bash
uv pip install --python .venv/bin/python playwright
.venv/bin/python -m playwright install chromium
```

- robots.txt가 막은 페이지는 열지 않고, 내부망 주소로 가는 요청과 이미지·글꼴은 막아요.
- Vercel 같은 서버리스에서는 쓰지 않아요. 그때는 참가 자격 문장을 붙여넣어 판정해요.
