"""GitHub로 로그인: 누가 승인했는지 GitHub가 확인해 준다.

- Bento GitHub App의 사용자 로그인(OAuth)을 쓴다. 따로 권한(scope)을 요청하지 않아서 받는 건 아이디뿐이다.
- 받은 GitHub 토큰은 아이디를 확인하는 데만 쓰고 바로 버린다. 저장하지 않는다.
- 로그인 상태는 서버가 서명한 쿠키(아이디·프로필 사진·만료 시각)로 기억한다.
"""

import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .proposals import ProposalError, sign, verify

router = APIRouter()
SESSION = "bento_session"
STATE = "bento_oauth_state"
SESSION_DAYS = 14


def _settings():
    from . import main
    return main.settings


def base_url(request: Request) -> str:
    import os
    if fixed := os.getenv("BENTO_PUBLIC_URL"):
        return fixed.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def current_user(request: Request) -> dict | None:
    """서명이 맞고 만료되지 않은 로그인 쿠키면 {login, avatar, name}."""
    tok = request.cookies.get(SESSION)
    if not tok:
        return None
    try:
        data = verify(tok, _settings().signing_key, max_age=SESSION_DAYS * 86400)
    except ProposalError:
        return None
    if data.get("kind") != "session" or not data.get("login"):
        return None  # 제안 토큰 등 다른 서명값을 로그인으로 쓰지 못하게
    return {"login": data["login"], "avatar": data.get("avatar"), "name": data.get("name")}


def exchange_code(code: str, redirect_uri: str) -> dict:
    """GitHub에서 받은 코드 → 사용자 아이디. 토큰은 여기서만 쓰고 버린다."""
    s = _settings()
    with httpx.Client(timeout=15, headers={"Accept": "application/json", "User-Agent": "bento-agent"}) as c:
        r = c.post("https://github.com/login/oauth/access_token",
                   data={"client_id": s.client_id, "client_secret": s.client_secret, "code": code, "redirect_uri": redirect_uri})
        token = (r.json() if r.status_code < 400 else {}).get("access_token")
        if not token:
            raise HTTPException(400, "GitHub 로그인을 확인하지 못했어요. 다시 시도해 주세요")
        u = c.get("https://api.github.com/user", headers={"Authorization": f"Bearer {token}"})
        if u.status_code >= 400:
            raise HTTPException(400, "GitHub 계정 정보를 읽지 못했어요")
        j = u.json()
    return {"login": j["login"], "avatar": j.get("avatar_url"), "name": j.get("name")}


@router.get("/auth/login")
def login(request: Request):
    s = _settings()
    if not s.login_enabled:
        raise HTTPException(404, "GitHub 로그인이 아직 설정되지 않았어요")
    state = secrets.token_urlsafe(24)
    q = urlencode({"client_id": s.client_id, "redirect_uri": base_url(request) + "/auth/callback", "state": state, "allow_signup": "true"})
    resp = RedirectResponse("https://github.com/login/oauth/authorize?" + q, status_code=302)
    resp.set_cookie(STATE, state, max_age=600, httponly=True, samesite="lax", secure=base_url(request).startswith("https"))
    return resp


@router.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = ""):
    s = _settings()
    if not s.login_enabled:
        raise HTTPException(404, "GitHub 로그인이 아직 설정되지 않았어요")
    expected = request.cookies.get(STATE)
    if not code or not state or not expected or not secrets.compare_digest(state, expected):
        raise HTTPException(400, "로그인 요청이 맞지 않아요. 처음부터 다시 시도해 주세요")
    user = exchange_code(code, base_url(request) + "/auth/callback")
    tok = sign({"kind": "session", **user, "exp": int(time.time()) + SESSION_DAYS * 86400}, s.signing_key)
    resp = RedirectResponse("/#home", status_code=302)
    resp.set_cookie("bento_just_login", "1", max_age=60, samesite="lax", secure=base_url(request).startswith("https"))  # 방금 로그인했으니 첫 화면은 건너뛴다 (주소에는 표시를 남기지 않는다)
    secure = base_url(request).startswith("https")
    resp.set_cookie(SESSION, tok, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=secure)
    resp.delete_cookie(STATE)
    return resp


@router.post("/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION)
    return resp
