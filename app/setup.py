"""GitHub App 만들기 도우미 (로컬 전용).

GitHub의 앱 매니페스트 기능으로 권한이 미리 채워진 생성 화면을 열고,
만들고 돌아오면 App ID·개인 키·웹훅 비밀값을 .env와 secrets/ 폴더에 저장한다.
배포된 서버에서는 동작하지 않는다 (localhost에서 연 경우만).
"""

import html
import json
import os
import secrets as pysecrets

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .config import ENV_FILE, ROOT, load_env_file, read_env_file

router = APIRouter()
_states: dict[str, dict] = {}

PERMISSIONS = {"checks": "read", "contents": "read", "issues": "write", "metadata": "read", "pull_requests": "write"}


def _local_only(request: Request) -> None:
    host = (request.url.hostname or "").lower()
    client = request.client.host if request.client else ""
    if host not in ("localhost", "127.0.0.1") or client not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(403, "설정 도우미는 내 컴퓨터(localhost)에서만 열 수 있어요")


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>
:root {{ color-scheme: light dark; --bg:#111214; --card:#1D1F23; --ink:#F2F3F5; --muted:#8D929B; --line:#2F3238; --pop:#F2C53D; --ok:#6CD49A; --bad:#FF8A73; }}
@media (prefers-color-scheme: light) {{ :root {{ --bg:#F4F5F2; --card:#fff; --ink:#17191C; --muted:#6A7069; --line:#DEE1DA; --ok:#2F7A4C; --bad:#B93B27; }} }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.6 system-ui, "Apple SD Gothic Neo", sans-serif; padding:24px max(16px, env(safe-area-inset-left)); }}
main {{ max-width:560px; margin:0 auto; display:flex; flex-direction:column; gap:14px; }}
h1 {{ font-size:24px; margin:0; }} .card {{ background:var(--card); border:1px solid var(--line); border-radius:16px; padding:16px; display:flex; flex-direction:column; gap:10px; }}
label {{ font-size:13px; color:var(--muted); }} input {{ height:44px; border-radius:10px; border:1px solid var(--line); background:var(--bg); color:var(--ink); padding:0 12px; font:inherit; }}
button, a.btn {{ height:46px; border-radius:99px; border:0; background:var(--pop); color:#2B2203; font:inherit; font-weight:700; padding:0 20px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; justify-content:center; }}
a.sub {{ background:var(--card); color:var(--ink); border:1px solid var(--line); }} small, .note {{ color:var(--muted); font-size:13px; }}
.ok {{ color:var(--ok); font-weight:700; }} .bad {{ color:var(--bad); font-weight:700; }} code {{ font-size:13px; }} ol {{ margin:0; padding-left:20px; }}
</style></head><body><main>{body}</main></body></html>""")


def _write_env(updates: dict[str, str]) -> None:
    cur = read_env_file()
    cur.update({k: v for k, v in updates.items() if v is not None})
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("# Bento 설정 도우미가 만든 파일. 비밀값이 들어 있으니 공유하지 마세요.\n")
        for k, v in cur.items():
            f.write(f"{k}={v}\n")
    os.chmod(ENV_FILE, 0o600)


@router.get("/setup")
def setup_home(request: Request):
    _local_only(request)
    from . import main
    s = main.settings
    if s.live:
        env = read_env_file()
        slug = env.get("GITHUB_APP_SLUG", "")
        return _page("Bento 설정", f"""<h1>GitHub 연결 설정</h1>
<div class="card"><b class="ok">앱이 만들어져 있어요</b><small>App ID {html.escape(s.app_id or '')} · 저장소 {html.escape(s.default_repo)}</small>
{f'<a class="btn sub" href="https://github.com/apps/{html.escape(slug)}/installations/new" target="_blank" rel="noopener">저장소에 설치하기 ↗</a>' if slug else ''}
<a class="btn" href="/setup/check">연결 확인하기</a></div>
<div class="card"><b class="{'ok' if s.login_enabled else ''}">GitHub 로그인 {'켜짐' if s.login_enabled else '꺼짐'}</b><small>팀원이 GitHub로 로그인하면 팀 접근 코드 없이, 저장소 팀원만 승인할 수 있어요.</small><a class="btn sub" href="/setup/login">{'다시 설정' if s.login_enabled else '로그인 켜기'}</a></div>
<div class="card"><b>설치하고 바로 연결되게 하기</b><small>앱 설정의 <b>Post installation → Setup URL</b>에 아래 주소를 넣고 <b>Redirect on update</b>를 켠 뒤 Save changes를 눌러 주세요. 그러면 앱을 설치하면서 고른 저장소가 Bento에 바로 연결돼요. 이 단계는 직접 해 주셔야 해요.</small><code>http://localhost:8787/github/installed</code>{f'<a class="btn sub" href="https://github.com/settings/apps/{html.escape(slug)}" target="_blank" rel="noopener">앱 설정 열기 ↗</a>' if slug else ''}</div>
<div class="card"><small>다른 저장소로 바꾸려면 <code>.env</code>의 <code>BENTO_REPO</code>를 고치고 서버를 다시 켜세요.</small></div>""")
    return _page("Bento 설정", """<h1>GitHub 연결 설정</h1>
<div class="card"><b>1. 앱 만들기</b>
<small>버튼을 누르면 GitHub의 앱 만들기 화면이 열려요. 권한은 Bento에 필요한 것만 미리 채워져 있어요: Contents 읽기, Pull requests·Issues 읽기/쓰기, Checks 읽기. 머지·푸시 권한은 없어요.</small>
<form action="/setup/github/start" method="get" style="display:flex;flex-direction:column;gap:8px">
<label for="name">앱 이름 (영문, 전 세계에서 겹치지 않게)</label><input id="name" name="name" required pattern="[A-Za-z0-9-]{3,34}" value="bento-""" + pysecrets.token_hex(3) + """">
<label for="repo">연결할 저장소 (owner/name) · 비워 두면 설치한 뒤에 골라요</label><input id="repo" name="repo" pattern="[^/\\s]+/[^/\\s]+" placeholder="내아이디/테스트저장소">
<label for="org">조직 저장소라면 조직 이름 (개인 저장소면 비워 두기)</label><input id="org" name="org" pattern="[A-Za-z0-9-]*">
<button type="submit">GitHub에서 앱 만들기</button></form></div>
<div class="card"><b>2. 저장소에 설치</b><small>앱을 만들고 돌아오면 설치 버튼을 보여 드려요.</small></div>""")


@router.get("/setup/github/start")
def setup_start(request: Request, name: str, repo: str = "", org: str = ""):
    _local_only(request)
    state = pysecrets.token_urlsafe(16)
    _states[state] = {"repo": repo.strip(), "org": org.strip()}
    base = f"{request.url.scheme}://{request.url.netloc}"
    manifest = {
        "name": name.strip(),
        "url": f"https://github.com/{repo.strip()}" if repo.strip() else "https://github.com",
        "description": "대회 공고부터 제출까지, 개발 팀플의 막힌 곳을 찾아 승인받고 대신 처리하는 에이전트",
        "hook_attributes": {"url": "https://example.com/api/github/webhook", "active": False},
        "redirect_url": f"{base}/setup/github/callback",
        "public": False,
        "default_permissions": PERMISSIONS,
        "default_events": [],
    }
    target = f"https://github.com/organizations/{html.escape(org.strip())}/settings/apps/new" if org.strip() else "https://github.com/settings/apps/new"
    return _page("GitHub로 이동", f"""<h1>GitHub로 이동하고 있어요</h1>
<form id="f" action="{target}?state={html.escape(state)}" method="post"><input type="hidden" name="manifest" value="{html.escape(json.dumps(manifest, ensure_ascii=False))}">
<div class="card"><small>자동으로 넘어가지 않으면 버튼을 눌러 주세요. GitHub 화면에서 <b>Create GitHub App</b>을 누르면 돌아와요.</small><button type="submit">GitHub에서 계속하기</button></div></form>
<script>document.getElementById("f").submit()</script>""")


@router.get("/setup/github/callback")
def setup_callback(request: Request, code: str, state: str):
    _local_only(request)
    info = _states.pop(state, None)
    if not info:
        raise HTTPException(400, "설정 요청을 찾지 못했어요. /setup 에서 다시 시작해 주세요")
    r = httpx.post(f"https://api.github.com/app-manifests/{code}/conversions", headers={"Accept": "application/vnd.github+json"}, timeout=20)
    if r.status_code >= 400:
        raise HTTPException(502, f"GitHub에서 앱 정보를 받지 못했어요 ({r.status_code})")
    app = r.json()
    os.makedirs(os.path.join(ROOT, "secrets"), exist_ok=True)
    pem_rel = os.path.join("secrets", f"{app['slug']}.pem")
    pem_path = os.path.join(ROOT, pem_rel)
    with open(pem_path, "w", encoding="utf-8") as f:
        f.write(app["pem"])
    os.chmod(pem_path, 0o600)
    cur = read_env_file()
    _write_env({
        "GITHUB_APP_ID": str(app["id"]),
        "GITHUB_APP_SLUG": app["slug"],
        "GITHUB_APP_PRIVATE_KEY_FILE": pem_rel,
        "GITHUB_WEBHOOK_SECRET": app.get("webhook_secret") or cur.get("GITHUB_WEBHOOK_SECRET") or pysecrets.token_hex(20),
        "BENTO_REPO": info["repo"] or None,
        "BENTO_SIGNING_KEY": cur.get("BENTO_SIGNING_KEY") or pysecrets.token_hex(32),
        "BENTO_ACCESS_CODE": cur.get("BENTO_ACCESS_CODE") or pysecrets.token_hex(3),
    })
    load_env_file(override=True)
    from . import main
    main.reload_settings()
    install = f"https://github.com/apps/{app['slug']}/installations/new"
    return _page("앱을 만들었어요", f"""<h1>앱을 만들었어요</h1>
<div class="card"><b class="ok">{html.escape(app['name'])} · App ID {app['id']}</b>
<small>개인 키는 <code>{html.escape(pem_rel)}</code>에, 나머지 설정은 <code>.env</code>에 저장했어요. 두 파일은 git과 배포에서 빠져 있어요.</small>
<small>팀 접근 코드: <code>{html.escape(read_env_file().get('BENTO_ACCESS_CODE', ''))}</code> · 팀원에게만 알려 주세요. 승인 버튼을 누를 때 물어봐요.</small></div>
<div class="card"><b>2. 저장소에 설치</b><small><b>Only select repositories</b>를 고르고 {('<code>' + html.escape(info['repo']) + '</code>') if info['repo'] else '테스트할 저장소'} 하나만 선택해 주세요.</small>
<a class="btn" href="{install}" target="_blank" rel="noopener">저장소에 설치하기 ↗</a></div>
<div class="card"><b>3. 연결 확인</b><small>설치를 마치면 눌러 주세요.</small><a class="btn sub" href="/setup/check">연결 확인하기</a></div>""")


@router.get("/setup/check")
def setup_check(request: Request):
    _local_only(request)
    from . import main
    s = main.settings
    if not s.live:
        return _page("연결 확인", '<h1>연결 확인</h1><div class="card"><b class="bad">아직 앱이 없어요</b><a class="btn" href="/setup">앱 만들기로 가기</a></div>')
    try:
        gh, st = main.state_for(None) if read_env_file().get("BENTO_REPO") else (None, None)
        if gh is None:
            raise HTTPException(404, "아직 저장소를 고르지 않았어요")
    except HTTPException as e:
        repos = _installed_repos(s)
        slug = read_env_file().get("GITHUB_APP_SLUG", "")
        install = f'<a class="btn sub" href="https://github.com/apps/{html.escape(slug)}/installations/new" target="_blank" rel="noopener">저장소에 설치하기 ↗</a>' if slug else ""
        if repos:
            items = "".join(f'<a class="btn sub" href="/setup/use?repo={html.escape(r)}">{html.escape(r)} 쓰기</a>' for r in repos)
            return _page("저장소 고르기", f"""<h1>저장소 고르기</h1><div class="card"><b>앱이 설치된 저장소예요. 연결할 곳을 골라 주세요.</b>{items}</div>
<div class="card"><small>목록에 없으면 설치할 때 그 저장소를 선택했는지 확인해 주세요.</small>{install}</div>""")
        return _page("연결 확인", f"""<h1>연결 확인</h1><div class="card"><b class="bad">아직 설치된 저장소가 없어요</b><small>{html.escape(str(e.detail))}</small>
<small>앱을 저장소에 설치한 뒤 다시 확인해 주세요.</small>{install}<a class="btn" href="/setup/check">다시 확인</a></div>""")
    return _page("연결 확인", f"""<h1>연결됐어요</h1><div class="card"><b class="ok">{html.escape(gh.repo)}</b>
<small>팀원 {len(st.members)}명 · 열린 PR {len(st.prs)}개 · 열린 이슈 {len(st.issues)}개 · 마일스톤 {len(st.milestones)}개를 읽었어요.</small>
<a class="btn" href="/">Bento 열기</a></div>""")


def _installed_repos(s) -> list[str]:
    """이 앱이 설치된 저장소 목록 (앱 자격 증명으로 조회)."""
    from .github import installed_repos
    try:
        return installed_repos(s, max_age=0)
    except Exception:  # noqa: BLE001 - 목록을 못 불러오면 설치 안내만 보여 준다
        return []


@router.get("/setup/use")
def setup_use(request: Request, repo: str):
    _local_only(request)
    from . import main
    if repo not in _installed_repos(main.settings):
        raise HTTPException(400, "앱이 설치되지 않은 저장소예요")
    _write_env({"BENTO_REPO": repo})
    load_env_file(override=True)
    main.reload_settings()
    return setup_check(request)


def _app_client_id(s) -> str | None:
    """연결된 GitHub 앱의 진짜 Client ID (공개 값). 앱 자격 증명이 없거나 못 읽으면 None."""
    if not s.live:
        return None
    import time

    import jwt
    try:
        now = int(time.time())
        tok = jwt.encode({"iat": now - 60, "exp": now + 300, "iss": s.app_id}, s.private_key, algorithm="RS256")
        r = httpx.get("https://api.github.com/app", headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}, timeout=10)
        return r.json().get("client_id") if r.status_code < 400 else None
    except Exception:  # noqa: BLE001 - 못 읽으면 직접 입력으로
        return None


def _check_client_id(cid: str, real: str | None) -> str | None:
    """문제가 있으면 안내 문구. GitHub 아이디를 넣는 실수가 흔하다."""
    if real and cid != real:
        return "Client ID가 Bento 앱과 달라요. GitHub 아이디가 아니라 앱 설정 위쪽의 Iv로 시작하는 값이에요"
    if not cid.startswith("Iv"):
        return "Client ID는 Iv로 시작해요. GitHub 아이디가 아니라 앱 설정 화면 위쪽의 Client ID를 넣어 주세요"
    return None


@router.get("/setup/login")
def setup_login(request: Request, ok: int = 0):
    """GitHub 로그인 켜기: 앱 설정에서 받은 Client ID·secret을 이 컴퓨터의 .env에만 저장한다."""
    _local_only(request)
    from . import main
    s = main.settings
    slug = read_env_file().get("GITHUB_APP_SLUG", "")
    app_url = f"https://github.com/settings/apps/{html.escape(slug)}" if slug else "https://github.com/settings/apps"
    cb = "http://localhost:8787/auth/callback"
    real = _app_client_id(s)
    cid_now = real or s.client_id or ""
    warn = ""
    if s.client_id and (msg := _check_client_id(s.client_id, real)):
        warn = f'<div class="card"><b class="bad">저장된 Client ID가 맞지 않아요</b><small>{html.escape(msg)}.{" 아래 칸에 맞는 값을 채워 두었어요." if real else ""}</small></div>'
    done = '<div class="card"><b class="ok">저장했어요. 이제 Bento 첫 화면에서 GitHub로 시작할 수 있어요.</b><a class="btn" href="/">Bento 열기</a></div>' if ok else ""
    return _page("GitHub 로그인 켜기", f"""<h1>GitHub 로그인 켜기</h1>{done}{warn}
<div class="card"><b>1. 앱 설정 열기</b><small>아래 버튼으로 Bento GitHub 앱 설정을 열어 주세요. 이 단계는 직접 해 주셔야 해요.</small>
<a class="btn sub" href="{app_url}" target="_blank" rel="noopener">앱 설정 열기 ↗</a></div>
<div class="card"><b>2. Redirect URI 등록</b><small>설정 화면의 <b>Identifying and authorizing users → Redirect URI</b> 칸(예전 이름 Callback URL)에 아래 주소를 넣고, 맨 아래 <b>Save changes</b>를 눌러 주세요. 그 아래 체크 상자들은 모두 끈 채로 두면 돼요. (배포 주소가 생기면 Add redirect URI로 하나 더 추가)</small><code>{cb}</code></div>
<div class="card"><b>3. Client secret 만들기</b><small>같은 화면의 <b>Client secrets → Generate a new client secret</b>을 누르면 한 번만 보여 줘요. 바로 아래 칸에 붙여 넣어 주세요. 채팅에는 붙이지 마세요.</small>
<form method="post" action="/setup/login" style="display:flex;flex-direction:column;gap:8px">
<label for="cid">Client ID (설정 화면 위쪽)</label><input id="cid" name="client_id" required pattern="[A-Za-z0-9._-]{{10,80}}" value="{html.escape(cid_now)}" autocomplete="off"{" readonly" if real else ""}>{'<small>연결된 앱에서 자동으로 채웠어요.</small>' if real else '<small>Iv로 시작하는 값이에요. GitHub 아이디가 아니에요.</small>'}
<label for="sec">Client secret</label><input id="sec" name="client_secret" type="password" required minlength="20" autocomplete="off">
<button class="btn" type="submit">이 컴퓨터에 저장하기</button></form>
<small>.env 파일에만 저장되고, 다른 곳으로 보내지 않아요.</small></div>""")


@router.post("/setup/login")
async def setup_login_save(request: Request):
    _local_only(request)
    origin = request.headers.get("origin") or ""
    if origin and not origin.startswith(("http://localhost", "http://127.0.0.1")):
        raise HTTPException(403, "이 컴퓨터의 설정 화면에서만 저장할 수 있어요")
    from urllib.parse import parse_qs
    form = parse_qs((await request.body()).decode("utf-8", "replace"))  # 추가 패키지 없이 폼 읽기
    cid, sec = (form.get("client_id") or [""])[0].strip(), (form.get("client_secret") or [""])[0].strip()
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9._-]{10,80}", cid) or len(sec) < 20 or "\n" in sec:
        raise HTTPException(400, "Client ID나 secret 모양이 맞지 않아요")
    from . import main as _m
    if msg := _check_client_id(cid, _app_client_id(_m.settings)):
        raise HTTPException(400, msg)
    _write_env({"GITHUB_CLIENT_ID": cid, "GITHUB_CLIENT_SECRET": sec})
    load_env_file(override=True)
    from . import main
    main.reload_settings()
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/setup/login?ok=1", status_code=303)
