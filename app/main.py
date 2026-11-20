"""Bento API. 화면(public/index.html)과 같은 주소에서 /api 아래로 동작한다."""

import hashlib
import hmac
import json
import os
import re
import threading
import time
from dataclasses import asdict

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import notice as notice_mod
from .actions import EDITABLE, STALE, ActionList, AssignIssue, CloseIssue, CreateIssue, CreateMilestone, TeamMessage, UpdateMilestone, execute, label, validate
from .forms import FormError, inspect_form
from . import submission as sub_mod
from . import progress as prog
from . import coord
from . import discover as disc
from . import ownership as own
from . import discord as discord_mod
from datetime import datetime, timedelta, timezone
from .actions import SetRoles, SetTeamRules, is_suspicious
from .config import load_settings
from .demo import DemoGitHub
from .fetch import FetchError, fetch_notice
from .schedule import work_plan
from .github import GitHub, GitHubError, installed_repos
from .proposals import ProposalError, sign, verify
from .state import build_state
from .triage import triage

settings = load_settings()
app = FastAPI(title="Bento", docs_url="/api/docs", openapi_url="/api/openapi.json")
_demo = DemoGitHub()
_clients: dict[str, GitHub] = {}


_EXECUTED: dict[str, float] = {}  # 실행한 제안 (같은 서버 안에서 두 번 실행 막기)
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _repo_lock(repo: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(repo, threading.Lock())


def reload_settings() -> None:
    """설정 도우미가 .env를 바꾼 뒤 서버를 다시 켜지 않고 반영한다."""
    global settings
    settings = load_settings()
    _clients.clear()


from .setup import router as setup_router  # noqa: E402
from .auth import current_user, router as auth_router  # noqa: E402

app.include_router(setup_router)
app.include_router(auth_router)


def gh_for(repo: str | None):
    repo = (repo or settings.default_repo).strip()
    if not settings.live:
        return _demo
    if repo.count("/") != 1:
        raise HTTPException(400, "저장소는 owner/name 형식으로 적어 주세요")
    if repo not in _clients:
        _clients[repo] = GitHub(settings, repo)
    return _clients[repo]


_STATE_CACHE: dict[str, tuple[float, object]] = {}
_STATE_LOCKS: dict[str, threading.Lock] = {}
STATE_TTL = 15  # 초: 홈이 열릴 때 한꺼번에 오는 여러 요청이 저장소를 매번 다시 읽지 않게


def _state_lock(key: str) -> threading.Lock:
    return _STATE_LOCKS.setdefault(key, threading.Lock())


def state_for(repo: str | None):
    gh = gh_for(repo)
    try:
        if not settings.live:  # 데모는 메모리라 바로 읽고, 테스트에서 상태를 바꿔 가며 본다
            return gh, build_state(gh)
        import copy
        with _state_lock(gh.repo):  # 같은 저장소를 동시에 읽는 요청은 한 번만 읽고 나눠 쓴다
            hit = _STATE_CACHE.get(gh.repo)
            if not hit or time.time() - hit[0] > STATE_TTL:
                hit = (time.time(), build_state(gh))
                _STATE_CACHE[gh.repo] = hit
        return gh, copy.deepcopy(hit[1])  # 판단 함수가 상태를 고쳐 쓰므로 복사본을 준다
    except GitHubError as e:
        if e.status == 404:
            raise HTTPException(404, f"{gh.repo}에 Bento 앱이 설치되지 않았거나 저장소가 없어요") from e
        raise HTTPException(502, f"GitHub에서 읽지 못했어요: {e}") from e


import contextvars

_ASKER: contextvars.ContextVar[str | None] = contextvars.ContextVar("asker", default=None)
# 팀 전체에 영향을 주는 변경: 제안한 사람 혼자서는 실행하지 못한다 (팀장이거나 다른 팀원이 승인해야)
TEAM_WIDE = {"set_team_rules", "update_milestone", "close_issue", "reassign_issue"}
APPROVE_PREFIX = "[승인 요청]"


@app.middleware("http")
async def _remember_asker(request: Request, call_next):
    """누가 제안을 만들었는지: 로그인한 사람(로그인을 켰을 때) 또는 화면에서 고른 아이디."""
    who = None
    try:
        if settings.login_enabled:  # 로그인을 켰으면 GitHub가 확인한 아이디만 믿는다 (화면이 보낸 이름은 누구나 적을 수 있다)
            who = (current_user(request) or {}).get("login") or None
        else:  # 로그인이 꺼진 데모·로컬: 화면의 '누구세요?'에서 고른 이름
            who = (request.headers.get("x-bento-user") or "").strip() or None
    except Exception:  # noqa: BLE001
        who = None
    tok = _ASKER.set(who)
    try:
        resp = await call_next(request)
        if request.method != "GET":  # 무언가를 바꾸는 요청 뒤에는 저장된 저장소 상태를 버려서 다음에 바로 새로 읽는다
            _STATE_CACHE.clear()
        return resp
    finally:
        _ASKER.reset(tok)


def _discord_hook(repo: str | None) -> str | None:
    """이 팀이 쓸 디스코드 웹훅(팀이 연결한 것 → 서버 기본값). 읽지 못하면 서버 기본값."""
    try:
        return discord_mod.webhook_for(gh_for(repo), settings)
    except Exception:  # noqa: BLE001 - 저장소를 못 읽어도 승인 화면은 떠야 한다
        return settings.discord_webhook


def _steps(actions: list, repo: str | None = None) -> list[dict]:
    disc = bool(_discord_hook(repo)) if any(a.type == "team_message" for a in actions) else bool(settings.discord_webhook)
    return [{"label": label(a, disc)[0], "sub": label(a, disc)[1], "edit": EDITABLE.get(a.type), "text": getattr(a, EDITABLE.get(a.type, "_"), None)} for a in actions]


def _proposal(repo: str, summary: str, actions: list, item_id: str | None = None) -> dict:
    by = _ASKER.get()
    token = sign({"repo": repo, "summary": summary, "item": item_id, "by": by, "actions": [a.model_dump() for a in actions]}, settings.signing_key)
    team = any(a.type in TEAM_WIDE for a in actions)
    return {"summary": summary, "steps": _steps(actions, repo), "token": token, "by": by, "team_wide": team}


@app.get("/api/health")
def health():
    return {"ok": True, "mode": "live" if settings.live else "demo", "repo": settings.default_repo,
            "ai": settings.anthropic_ready, "discord": bool(settings.discord_webhook), "needs_code": bool(settings.access_code) and not settings.login_enabled, "login": settings.login_enabled, "install_url": _install_url()}


class DiscordIn(BaseModel):
    repo: str | None = None
    url: str = ""


def _member_actor(request: Request, st, what: str) -> str | None:
    """로그인을 켠 서버에서 이 일을 할 수 있는 사람: 로그인한 그 저장소 팀원. 로그인이 꺼져 있으면 None."""
    if not settings.login_enabled:
        return None
    u = current_user(request)
    if not u:
        raise HTTPException(401, {"message": "GitHub로 로그인해 주세요", "login": True})
    if u["login"] not in st.members:
        raise HTTPException(403, f"이 저장소 팀원만 {what} 수 있어요")
    return u["login"]


def _discord_actor(request: Request, st) -> str | None:
    """디스코드 연결을 바꿀 수 있는 사람: 로그인했다면 그 저장소 팀원."""
    return _member_actor(request, st, "디스코드를 연결할")


@app.get("/api/discord")
def discord_status(repo: str | None = None):
    """이 팀이 디스코드에 연결돼 있는지. 주소는 알려 주지 않는다."""
    gh = gh_for(repo)
    team = discord_mod.read(gh, settings.signing_key) is not None
    return {"connected": team or bool(settings.discord_webhook), "source": "team" if team else ("server" if settings.discord_webhook else None)}


@app.post("/api/discord/connect")
def discord_connect(body: DiscordIn, request: Request, x_bento_code: str | None = Header(default=None)):
    """웹훅 주소를 확인하고 이 팀에 연결한다. 메시지는 보내지 않는다. 주소는 암호화해서 팀 저장소 라벨에 저장."""
    _check_code(x_bento_code, request)
    gh, st = state_for(body.repo)
    actor = _discord_actor(request, st)
    try:
        info = discord_mod.check(body.url)
        discord_mod.write(gh, body.url, settings.signing_key)
    except discord_mod.DiscordError as e:
        raise HTTPException(e.status, str(e)) from e
    except GitHubError as e:
        raise HTTPException(502, f"GitHub에 저장하지 못했어요: {e}") from e
    return {"connected": True, "source": "team", "name": info["name"], "by": actor}


@app.post("/api/discord/disconnect")
def discord_disconnect(body: DiscordIn, request: Request, x_bento_code: str | None = Header(default=None)):
    _check_code(x_bento_code, request)
    gh, st = state_for(body.repo)
    _discord_actor(request, st)
    try:
        discord_mod.clear(gh)
    except GitHubError as e:
        raise HTTPException(502, f"GitHub에서 지우지 못했어요: {e}") from e
    return {"connected": bool(settings.discord_webhook), "source": "server" if settings.discord_webhook else None}


@app.get("/api/me")
def me(request: Request, repo: str | None = None):
    """GitHub로 로그인한 사람. 실제 저장소면 그 저장소 팀원인지도 알려 준다."""
    user = current_user(request)
    if not user:
        return {"logged_in": False, "login_enabled": settings.login_enabled}
    member = True
    if settings.live:
        try:
            member = user["login"] in gh_for(repo).members()
        except (GitHubError, HTTPException):
            member = False
    return {"logged_in": True, "login_enabled": settings.login_enabled, **user, "member": member}


def _install_url() -> str | None:
    slug = os.getenv("GITHUB_APP_SLUG")
    return f"https://github.com/apps/{slug}/installations/new" if settings.live and slug else None


@app.get("/github/installed")
def github_installed(installation_id: int = 0, setup_action: str = ""):
    """GitHub 앱을 설치하고 돌아오는 곳 (앱 설정의 Setup URL).
    방금 고른 저장소를 화면에 넘겨서, 사용자가 목록에서 다시 고르지 않아도 연결되게 한다."""
    from urllib.parse import quote

    from fastapi.responses import RedirectResponse

    from .github import installation_repos
    if not settings.live or not installation_id:
        return RedirectResponse("/#chat", status_code=302)
    try:
        repos = installation_repos(settings, installation_id)
    except Exception:  # noqa: BLE001 - 못 읽으면 평소처럼 목록에서 고르게 한다
        repos = []
    return RedirectResponse("/?installed=" + quote(",".join(repos[:20]), safe=",/") + "#chat", status_code=302)


@app.get("/api/projects")
def projects():
    """진행 중인 팀플 = Bento 앱이 설치된 저장소. 저장소마다 연결된 공고와 제출 마일스톤을 함께 준다."""
    if settings.live:
        try:
            repos = installed_repos(settings)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, "설치된 저장소 목록을 읽지 못했어요") from e
    else:
        repos = [_demo.repo]
    out = []
    for r in repos:
        p = {"repo": r, "notice": None, "due": None, "done": None, "total": None}
        try:
            ms = gh_for(r).milestones()
        except (GitHubError, HTTPException):
            p["error"] = True
            out.append(p)
            continue
        ex = notice_mod.from_descriptions([m.get("description") or "" for m in ms])
        sub = next((m for m in ms if m["title"] == "해커톤 제출"), None)
        p["notice"] = ex.title if ex else None
        if sub:
            done, left = sub.get("closed_issues", 0), sub.get("open_issues", 0)
            p.update(due=sub.get("due_on"), done=done, total=done + left)
        out.append(p)
    return {"projects": out, "default": settings.default_repo if settings.live else _demo.repo, "install_url": _install_url()}


@app.get("/api/state")
def get_state(repo: str | None = None):
    gh, st = state_for(repo)
    return {"repo": gh.repo, "members": st.members, "prs": [asdict(p) for p in st.prs], "issues": [asdict(i) for i in st.issues],
            "milestones": [asdict(m) for m in st.milestones]}


@app.get("/api/triage")
def get_triage(repo: str | None = None):
    gh, st = state_for(repo)
    items = triage(st, settings.review_wait_hours)
    now = datetime.now(timezone.utc)
    rules = own.read_rules(gh)
    for it in items:
        owner, age = own.owner_and_age(it, st, settings.review_wait_hours, now)
        it.update(owner=owner, owner_why=own.owner_reason(it, st, owner, now), age_hours=round(age, 1), level=own.level(age))
        first = next((o for o in it["options"] if o.get("recommended")), it["options"][0] if it["options"] else None)
        it["lazy"] = own.lazy_status(gh, it, first["actions"], rules, st.members, now) if first and rules["lazy"] else {"eligible": False}
        it["options"] = [{"label": o["label"], "recommended": o.get("recommended", False), "sub": o.get("sub", ""),
                          "proposal": _proposal(gh.repo, o["label"], o["actions"], it["id"])} for o in it["options"]]
    for it in _approval_items(gh, st):  # 다른 팀원이 보낸 승인 요청: 팀장(제안자가 아니면)이 먼저, 아니면 팀 누구나
        lead = next((m for m in st.leads if m != it["proposed_by"]), None)
        it.update(owner=lead, age_hours=0, level=0 if lead else 2, lazy={"eligible": False})
        items.insert(0, it)
    return {"repo": gh.repo, "items": items, "members": st.members, "duty": own.duty(st.members, now, st.rules), "duty_schedule": own.duty_schedule(st.members, st.rules, now), "rules": rules, "roles": st.roles, "leads": st.leads}


def _why_assigned(title: str, who: str, st) -> str:
    """이 사람이 왜 이 일을 맡았는지. Bento가 정한 게 아니라 GitHub의 담당자 지정을 그대로 보여 준다."""
    named = own.named_owner(title, st.members, getattr(st, "names", None), st.leads)
    parts = ["제목에 담당으로 적혀 있어요" if named == who else "GitHub 담당자로 지정돼 있어요"]
    mine = [c for c in own.role_codes_for(title) if c in st.roles.get(who, [])]
    if mine:
        parts.append(f"{own.role_names(mine)} 역할과 맞아요")
    return " · ".join(parts)


@app.get("/api/board")
def board(repo: str | None = None):
    from .notice import is_submit_act
    """홈 화면용: 팀원별 할 일(마감 순)·늦은 일, 담당 없는 일, 일이 몰렸으면 다시 나누기 제안."""
    import math

    from .actions import ReassignIssue
    from .state import ms_due
    gh, st = state_for(repo)
    now = datetime.now(timezone.utc)
    due_of = {m.title: ms_due(m.due_on) for m in st.milestones}
    people = {m: {"login": m, "roles": st.roles.get(m, []), "lead": m in st.leads, "tasks": []} for m in st.members}
    unassigned, raw = [], {}
    for i in st.issues:
        if i.title.startswith(APPROVE_PREFIX):
            continue
        due = due_of.get(i.milestone)
        from .actions import is_suspicious
        t = {"number": i.number, "title": i.title, "url": i.url, "due": due.isoformat() if due else None, "late": bool(due and due < now), "assignees": i.assignees,
             "safe": not is_suspicious(i.body)}
        raw[i.number] = i
        for who in i.assignees:
            if who in people:
                people[who]["tasks"].append({**t, "why": _why_assigned(i.title, who, st)})
        if not i.assignees and t["safe"] and not is_submit_act(re.sub(r"^\[[^\]]*\]\s*", "", i.title)):
            unassigned.append(t)  # 의심 지시가 있는 이슈·제출 행동은 담당을 나누지 않는다 ('이슈 정리'에서 따로 다룬다)
    key = lambda t: (t["due"] is None, t["due"] or "")
    for p in people.values():
        p["tasks"].sort(key=key)
        p["open"], p["late"] = len(p["tasks"]), sum(t["late"] for t in p["tasks"])
    unassigned.sort(key=key)
    load0 = {m: people[m]["open"] for m in people}
    for t in unassigned:  # 담당 없는 할 일: 제목으로 역할을 보고, 그 역할에서 맡은 일이 적은 사람을 추천
        if re.search(r"\(\s*(?:담당\s*[:：]\s*)?전원\s*\)|담당\s*[:：]\s*전원", t["title"]):  # '(전원)'은 모두 함께 하는 일: 한 사람에게 추천하지 않는다
            t["suggest"] = {"who": None, "role": "", "why": "전원이 함께 하는 일이라 한 사람에게 맡기지 않아요"}
            continue
        named = own.named_owner(t["title"], st.members, getattr(st, "names", None), st.leads)
        if named == "?":  # 제목에 '담당: A·발표'처럼 적혀 있지만 누군지 모르면 추천하지 않는다
            t["suggest"] = {"who": None, "role": own.role_names(own.role_codes_for(t["title"])), "why": "제목에 담당이 적혀 있어요 · 직접 정해 주세요"}
            continue
        who, why = (named, "제목에 적힌 담당") if named else own.pick_by_role(st.members, st.roles, load0, t["title"])
        if who:
            load0[who] = load0.get(who, 0) + 1
        t["suggest"] = {"who": who, "role": own.role_names(own.role_codes_for(t["title"])), "why": why}
    # 다시 나누기: 평균보다 2개 넘게 많이 맡은 사람(3개 이상)이나 늦은 일이 2개 이상인 사람의 일을, 역할 맞고 한가한 사람에게
    rebalance, why = None, []
    if len(st.members) > 1:
        avg = sum(p["open"] for p in people.values()) / len(people)
        load = {m: people[m]["open"] for m in people}
        actions = []
        for p in sorted(people.values(), key=lambda x: -x["open"]):
            heavy = p["open"] >= 3 and p["open"] >= avg + 2
            if not (heavy or p["late"] >= 2):
                continue
            give = max(1, p["open"] - math.ceil(avg)) if heavy else 1
            for t in sorted(p["tasks"], key=lambda t: (not t["late"], key(t)))[:give]:
                if len(t["assignees"]) != 1 or not t["safe"]:  # 의심스러운 지시가 있는 이슈는 건드리지 않는다
                    continue
                to, reason = own.pick_by_role(st.members, st.roles, load, t["title"], skip=[p["login"]])
                if not to or load[to] + 1 >= load[p["login"]]:
                    continue
                load[to] += 1
                load[p["login"]] -= 1
                actions.append(ReassignIssue(issue=t["number"], users=[to], expect_assignees=[p["login"]]))
            why.append(f"{p['login']} 님이 {p['open']}개를 맡고 있어요" + (f" (늦음 {p['late']})" if p["late"] else ""))
        if actions:
            rebalance = {"why": " · ".join(why), "proposal": _proposal(gh.repo, "일이 몰린 사람의 할 일 다시 나누기", actions, "rebalance")}
    return {"repo": gh.repo, "people": list(people.values()), "unassigned": unassigned, "leads": st.leads,
            "roles": {k: own.role_names(v) for k, v in st.roles.items()}, "rebalance": rebalance}


class NoticeIn(BaseModel):
    text: str
    repo: str | None = None
    new_project: bool = False  # 아직 저장소가 없는 새 팀플. 저장소를 읽지 않고 공고만 분석한다.
    no_ai: bool = False  # 출처가 AI 크롤러를 막아 둔 글: AI에 보내지 않고 규칙으로만 읽는다
    submit_deadline_iso: str | None = None  # 공고에서 못 찾은 제출 마감을 사용자가 직접 적은 것 (있으면 읽은 값 대신 쓴다)
    url: str | None = None  # 공고를 읽어 온 주소(있으면 마일스톤에 적어 둬서 서버가 마감 변경을 지켜본다)


@app.post("/api/notice")
def analyze_notice(body: NoticeIn, request: Request, x_bento_code: str | None = Header(default=None)):
    gh, st = None, None
    if not body.new_project:
        try:
            gh, st = state_for(body.repo)
        except HTTPException:
            pass

    def guard():  # AI를 실제로 부를 때만: 로그인(또는 팀 접근 코드) 확인 + 횟수 제한. 캐시·규칙으로 읽을 때는 묻지 않는다
        _check_code(x_bento_code, request)
        u = current_user(request) if settings.login_enabled else None
        notice_mod.allow_ai((u or {}).get("login") or (request.client.host if request.client else "anon"))

    try:
        ex, dropped, source, usage = notice_mod.extract(body.text, [m.description for m in st.milestones] if st else None, allow_ai=not body.no_ai, guard=guard)
    except notice_mod.NoticeLimit as e:
        raise HTTPException(429, str(e)) from e
    except notice_mod.NoticeError as e:
        raise HTTPException(422, str(e)) from e
    if body.submit_deadline_iso:
        try:
            iso = notice_mod.aware_iso(body.submit_deadline_iso)
            from .rules import human as _human
            ex = ex.model_copy(update={"submit_deadline_iso": iso, "submit_deadline": _human(datetime.fromisoformat(iso))})
        except (ValueError, TypeError) as e:
            raise HTTPException(422, "날짜 형식이 맞지 않아요") from e
    team = len(st.members) if st else None
    team_ok = None if team is None or (ex.team_min is None and ex.team_max is None) else (
        (ex.team_min is None or team >= ex.team_min) and (ex.team_max is None or team <= ex.team_max))
    out = {"extract": ex.model_dump(), "dropped": dropped, "source": source, "usage": usage, "team_size": team, "team_ok": team_ok, "repo": gh.repo if gh else None}
    if gh and st:
        actions, linked = notice_mod.plan(ex, st, h=notice_mod.notice_hash(notice_mod.clean_text(body.text)), src=body.url)
        if not st.milestone("해커톤 제출"):
            out["plan"] = _proposal(gh.repo, "대회 일정을 GitHub에 적어 두기", actions, "notice")
            out["linked"] = linked
    return out


def _check_code(code: str | None, request: Request | None = None) -> None:
    if settings.login_enabled and request is not None and current_user(request):
        return  # GitHub로 로그인했으면 팀 접근 코드가 필요 없다
    if settings.login_enabled and not settings.access_code:
        raise HTTPException(401, {"message": "GitHub로 로그인해 주세요", "login": True})
    if settings.access_code and not hmac.compare_digest(code or "", settings.access_code):
        raise HTTPException(401, "팀 접근 코드가 맞지 않아요")


class FetchIn(BaseModel):
    url: str
    title: str | None = None  # 이미 아는 대회 이름 (웹 검색할 때 쓴다)
    no_ai: bool = False  # 출처가 AI 사용을 막아 둔 글이면 웹 검색도 하지 않는다


THIN = 200  # 이보다 짧으면 페이지를 거의 못 읽은 것


@app.post("/api/notice/fetch")
def fetch_url(body: FetchIn, request: Request, x_bento_code: str | None = Header(default=None)):
    """공고 링크에서 본문을 가져온다. 서버가 대신 여는 주소라 로그인이나 팀 접근 코드가 있어야 한다.

    화면을 스크립트로 그리는 사이트라 글을 거의 못 읽었으면, AI 웹 검색으로 출처 문장을 모아 대신 돌려준다.
    """
    from . import websearch
    _check_code(x_bento_code, request)
    try:
        page = fetch_notice(body.url)
    except FetchError as e:
        raise HTTPException(422, str(e)) from e
    body_text = (page.get("text") or "").replace(page.get("title") or "", "").strip()
    if len(body_text) < THIN and not body.no_ai and websearch.allowed(page.get("url") or body.url) and websearch.ready():
        u = current_user(request) if settings.login_enabled else None
        who = (u or {}).get("login") or (request.client.host if request.client else "anon")
        try:
            found = websearch.search_notice(page.get("url") or body.url, body.title or page.get("title"), who=who)
        except websearch.SearchError as e:
            page["note"] = str(e)
            found = None
        if found:
            page.update(text=found["text"], searched=True, sources=found["sources"],
                        note=f"이 페이지는 화면을 스크립트로 그려서 직접 읽지 못했어요. AI가 웹 검색으로 찾은 출처 {len(found['sources'])}곳의 문장으로 채웠어요. 공식 원문과 다를 수 있으니 출처를 확인해 주세요.")
    return page


class FileIn(BaseModel):
    name: str
    mime: str | None = None
    data: str  # base64


@app.post("/api/notice/file")
def notice_file(body: FileIn, request: Request, x_bento_code: str | None = Header(default=None)):
    """포스터·첨부파일(HWP·HWPX·PDF·사진)에서 공고 글을 꺼낸다. 꺼낸 글은 /api/notice로 분석한다.

    한글 파일은 서버에서 글자만 뽑고(AI 없음), 사진·PDF는 AI가 옮겨 적는다(비용이 들어 로그인·접근 코드 필요).
    """
    import base64
    import binascii
    from . import attach, websearch
    if len(body.data) > attach.MAX_BYTES * 4 // 3 + 8:
        raise HTTPException(413, "파일이 너무 커요(3MB까지)")
    try:
        raw = base64.b64decode(body.data, validate=True)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(422, "파일을 읽지 못했어요") from e
    try:
        ai = attach.kind_of(body.name, body.mime) in ("pdf", "image")
        if ai:
            _check_code(x_bento_code, request)
        u = current_user(request) if settings.login_enabled else None
        who = (u or {}).get("login") or (request.client.host if request.client else "anon")
        return attach.read_file(body.name, body.mime, raw, who=who, ai_ready=ai and websearch.ready())
    except attach.AttachError as e:
        raise HTTPException(e.status, str(e)) from e


class ChatIn(BaseModel):
    message: str
    history: list[dict] | None = None  # 이 자유 대화에서 오간 말만 [{role, text}]
    context: dict | None = None  # 팀 상황 요약 {stage, apply_days, submit_days, late} — 허용된 값만 쓴다


@app.post("/api/chat")
def chat_free(body: ChatIn, request: Request, x_bento_code: str | None = Header(default=None)):
    """정해진 낱말로 못 알아들은 말에 짧게 답하고, 맞는 Bento 기능 하나를 고른다. 실행은 하지 않는다."""
    from . import chat as chat_mod
    _check_code(x_bento_code, request)
    u = current_user(request) if settings.login_enabled else None
    who = (u or {}).get("login") or (request.client.host if request.client else "anon")
    try:
        return chat_mod.ask(body.message, body.history, who=who, context=body.context)
    except chat_mod.ChatError as e:
        raise HTTPException(e.status, str(e)) from e


class RecheckIn(BaseModel):
    url: str
    base: dict | None = None  # 지난번에 읽은 값 {hash, apply_deadline_iso, submit_deadline_iso}


@app.post("/api/notice/recheck")
def notice_recheck(body: RecheckIn, request: Request, x_bento_code: str | None = Header(default=None)):
    """공고가 바뀌었는지 (하루 한 번). AI를 쓰지 않고 규칙으로만 다시 읽는다.

    같은 규칙으로 읽은 지난번 값과 비교하므로, 처음 분석이 AI였어도 규칙 차이 때문에 '바뀌었다'고 하지 않는다.
    본문이 바뀌어도 마감이 그대로면 알리지 않는다 (조회수·댓글 같은 변화는 무시).
    """
    _check_code(x_bento_code, request)
    try:
        page = fetch_notice(body.url)
    except FetchError as e:
        raise HTTPException(422, str(e)) from e
    text = notice_mod.clean_text(page["text"])
    h = notice_mod.notice_hash(text)
    from .rules import rule_extract
    try:
        ex = rule_extract(text)
        now_vals = {"apply_deadline_iso": ex.apply_deadline_iso, "submit_deadline_iso": ex.submit_deadline_iso}
    except Exception:  # noqa: BLE001 - 날짜를 못 읽으면 비교하지 않는다
        now_vals = {"apply_deadline_iso": None, "submit_deadline_iso": None}
    cur = {"hash": h, **now_vals}
    base = body.base or {}
    if not base.get("hash"):
        return {"changed": False, "first": True, "base": cur, "diffs": []}
    diffs = []
    if h != base["hash"]:
        for k, label in (("apply_deadline_iso", "신청 마감"), ("submit_deadline_iso", "제출 마감")):
            if base.get(k) and now_vals[k] and base[k] != now_vals[k]:
                diffs.append({"field": k, "label": label, "old": base[k], "new": now_vals[k]})
    return {"changed": bool(diffs), "first": False, "base": cur, "diffs": diffs, "text_changed": h != base["hash"]}


class ScheduleIn(BaseModel):
    extract: notice_mod.NoticeExtract


@app.post("/api/schedule")
def schedule(body: ScheduleIn):
    """제출 마감에서 거꾸로 계산한 작업 계획. AI를 쓰지 않는다."""
    return {"steps": work_plan(body.extract)}


class Step(BaseModel):
    title: str
    due: str
    note: str = ""


class ScheduleGitHubIn(BaseModel):
    repo: str | None = None
    steps: list[Step]


@app.post("/api/schedule/github")
def schedule_to_github(body: ScheduleGitHubIn):
    """계획 단계를 저장소 마일스톤으로 만드는 제안. 실행은 승인 뒤에만."""
    from datetime import datetime
    gh, st = state_for(body.repo)
    have = {m.title for m in st.milestones}
    titles = {i.title for i in st.issues}
    # 사람별로 나누기: 지금 맡은 열린 이슈가 적은 사람부터 한 단계씩 돌아가며 맡긴다
    load = {m: sum(1 for i in st.issues if m in i.assignees) for m in st.members}
    actions, who = [], []
    for s in body.steps[:12]:
        title = f"계획 · {s.title.strip()[:50]}"
        try:
            due = notice_mod._utc(datetime.fromisoformat(s.due).isoformat())
        except ValueError as e:
            raise HTTPException(400, f"날짜를 읽지 못했어요: {s.due}") from e
        if title not in have:
            actions.append(CreateMilestone(title=title, due_on=due, description="Bento가 공고 마감에서 거꾸로 짠 작업 계획"))
        issue_title = f"[계획] {s.title.strip()[:60]}"
        if issue_title not in titles:
            owner = own.pick_by_role(st.members, st.roles, load, s.title)[0] if load else None
            if owner:
                load[owner] += 1
                who.append(owner)
            actions.append(CreateIssue(title=issue_title, body=(s.note or "") + f"\n\n마감: {s.due[:16].replace('T', ' ')}\n\n끝나면 이 이슈를 닫아 주세요. 마감까지 안 닫히면 Bento가 계획을 다시 짜자고 알려요.",
                                       assignees=[owner] if owner else [], milestone_title=title))
    if not actions:
        return {"proposal": None, "message": "계획 단계와 할 일이 이미 모두 저장소에 있어요"}
    return {"proposal": _proposal(gh.repo, "작업 계획을 GitHub에 적고 사람별로 나누기", actions, "schedule"),
            "assignees": sorted(set(who)), "solo": len(st.members) <= 1, "by_role": bool(st.roles)}


class ProgressIn(BaseModel):
    repo: str | None = None
    plan: list[dict] = []
    deadline_iso: str | None = None
    plan_start_iso: str | None = None


def _progress_proposal(gh, r: dict):
    actions = []
    for mv in r["moves"]:
        if mv["repo"]:
            actions.append(UpdateMilestone(title=prog.PLAN_PREFIX + mv["title"], due_on=notice_mod._utc(mv["new"]), reason="늦어진 단계를 남은 시간에 맞춰 다시 놓아요",
                                           expect_due=mv.get("raw_due") or notice_mod._utc(mv["old"])))
    for a in r["assign"]:
        actions.append(AssignIssue(issue=a["issue"], users=[a["user"]], expect_assignees=[]))
    if actions and r["summary"]:
        actions.append(TeamMessage(text=r["summary"]))
    return _proposal(gh.repo, "늦어진 계획 다시 짜기", actions, "replan") if actions else None


@app.post("/api/progress")
def progress(body: ProgressIn):
    """계획 대비 진행 점검 → 다시 짠 계획 제안. 규칙으로 판단해서 토큰이 들지 않는다."""
    gh, st = state_for(body.repo)
    plan = [s for s in body.plan[:20] if isinstance(s.get("title"), str) and isinstance(s.get("due"), str)]
    try:
        r = prog.check(st, plan, body.deadline_iso, plan_start_iso=body.plan_start_iso)
    except ValueError as e:
        raise HTTPException(400, "계획 날짜를 읽지 못했어요") from e
    r["proposal"] = _progress_proposal(gh, r)
    return r


class RulesIn(BaseModel):
    repo: str | None = None
    lazy: list[str] = []
    hours: int = 24
    rotate_days: int | None = None
    reshuffle: bool = False


class RolesIn(BaseModel):
    repo: str | None = None
    roles: dict[str, list[str]]


@app.get("/api/roles")
def roles_get(repo: str | None = None):
    """팀 역할표와 고를 수 있는 역할."""
    gh, st = state_for(repo)
    return {"roles": st.roles, "members": st.members, "choices": own.ROLES}


@app.post("/api/roles")
def roles_set(body: RolesIn, request: Request, x_bento_code: str | None = Header(default=None)):
    """팀 역할 바꾸기. 저장 버튼을 누른 것이 확인이라 따로 승인을 받지 않는다. 로그인을 켠 서버에서는 그 저장소 팀원만."""
    _check_code(x_bento_code, request)
    gh, st = state_for(body.repo)
    actor = _discord_actor(request, st)
    roles = {m: [c for c in dict.fromkeys(cs) if c in own.ROLES][:3] for m, cs in body.roles.items() if m in st.members}
    changed = {m: c for m, c in roles.items() if sorted(c) != sorted(st.roles.get(m, []))}
    if not changed:
        return {"saved": False, "message": "바뀐 역할이 없어요", "roles": st.roles}
    try:
        own.write_roles(gh, changed)
    except GitHubError as e:
        raise HTTPException(502, f"GitHub에 저장하지 못했어요: {e}") from e
    merged = {**st.roles, **changed}
    return {"saved": True, "roles": {m: c for m, c in merged.items() if c}, "changed": sorted(changed), "by": actor}


FOOT_UNDO = "\n\n<sub>— 캘린더에서 누른 것을 Bento가 대신 남겼어요</sub>"


class StepDoneIn(BaseModel):
    repo: str | None = None
    milestone: str  # 끝낸 단계의 마일스톤 이름 ('계획 · …' 또는 '해커톤 제출')
    reopen: list[int] = []  # 되돌리기: 방금 이 요청으로 닫았던 이슈 번호


@app.post("/api/plan/done")
def plan_done(body: StepDoneIn, request: Request, x_bento_code: str | None = Header(default=None)):
    """캘린더에서 '끝났어요': 그 단계(마일스톤)의 열린 이슈를 닫는다. 누른 것이 확인이라 승인은 받지 않고,
    누가 닫았는지 이슈마다 댓글로 남긴다. 되돌리기는 방금 닫은 이슈만 다시 연다."""
    _check_code(x_bento_code, request)
    gh, st = state_for(body.repo)
    actor = _member_actor(request, st, "단계를 끝낼") or _ASKER.get() or "팀원"
    if body.reopen:  # 되돌리기
        done = []
        for n in dict.fromkeys(body.reopen):
            try:
                issue = gh.issue(n)
                if issue.get("state") != "closed" or (issue.get("milestone") or {}).get("title") != body.milestone or issue.get("pull_request"):
                    continue
                gh.update_issue(n, state="open")
                gh.comment(n, f"{actor}님이 캘린더에서 되돌려서 다시 열었어요.{FOOT_UNDO}")
                done.append(n)
            except GitHubError as e:
                if e.status == 404:  # 없는 번호는 건너뛴다
                    continue
                raise HTTPException(502, f"GitHub에서 다시 열지 못했어요: {e}") from e
        return {"reopened": done, "milestone": body.milestone}
    if not st.milestone(body.milestone):
        return {"closed": [], "skipped": [], "milestone": body.milestone, "message": "저장소에 이 단계의 마일스톤이 없어서 이 기기에서만 완료로 표시했어요"}
    closed, skipped = [], []
    for i in [x for x in st.issues if x.milestone == body.milestone]:
        if is_suspicious(i.body):
            skipped.append({"number": i.number, "why": "의심스러운 지시가 있어 건드리지 않았어요"})
            continue
        try:
            gh.comment(i.number, f"{actor}님이 캘린더에서 '{body.milestone}' 단계를 끝냈다고 표시해서 닫았어요. 잘못 눌렀다면 이 이슈를 다시 열어 주세요.{FOOT_UNDO}")
            gh.update_issue(i.number, state="closed", state_reason="completed")
            closed.append(i.number)
        except GitHubError as e:
            skipped.append({"number": i.number, "why": f"GitHub 오류: {e}"})
    return {"closed": closed, "skipped": skipped, "milestone": body.milestone, "by": actor}


@app.post("/api/team-rules")
def team_rules(body: RulesIn):
    """'반대가 없으면 실행' 규칙 바꾸기. 팀 전체에 영향을 주니 이것도 승인을 받는다."""
    gh, st = state_for(body.repo)
    lazy = [c for c in dict.fromkeys(body.lazy) if c in own.CATEGORIES]
    return {"proposal": _proposal(gh.repo, "팀 규칙 바꾸기", [SetTeamRules(lazy=lazy, hours=body.hours, rotate_days=body.rotate_days, reshuffle=body.reshuffle)], "rules"), "current": own.read_rules(gh)}


def lazy_tick(repo: str | None, now: datetime | None = None) -> dict:
    """반대가 없으면 실행: 12시간 넘게 승인이 없는 일에 예고 댓글을 달고, 정한 시간 동안 👎가 없으면 실행한다."""
    now = now or datetime.now(timezone.utc)
    gh, st = state_for(repo)
    rules = own.read_rules(gh)
    out = {"posted": [], "ran": [], "vetoed": []}
    if not rules["lazy"]:
        return out
    for it in triage(st, settings.review_wait_hours):
        owner, age = own.owner_and_age(it, st, settings.review_wait_hours, now)
        first = next((o for o in it["options"] if o.get("recommended")), it["options"][0] if it["options"] else None)
        if not first or own.level(age) < 1 or not own.lazy_ok(first["actions"], rules):
            continue
        status = own.lazy_status(gh, it, first["actions"], rules, st.members, now)
        if not status.get("status"):
            continue  # 번호가 없는 일(팀원 안부 등)은 '반대 없으면 실행'을 쓰지 않는다
        if status["status"] == "none":  # 예고: 지금부터 정한 시간 동안 반대를 받는다
            key = own.key_of(gh.repo, first["actions"])
            gh.comment(status["number"], own.notice_text(owner, first["label"], rules["hours"], key, now))
            out["posted"].append(it["id"])
        elif status["status"] == "vetoed":
            out["vetoed"].append(it["id"])
        elif status["due"]:
            try:
                res = _run(gh.repo, first["actions"], by="lazy")
                out["ran"].append({"id": it["id"], "ok": all(r["ok"] for r in res)})
            except HTTPException:
                pass  # 그사이 누가 처리했거나 다른 서버가 실행 중이면 다음 기회에
    return out


class TickIn(BaseModel):
    repo: str | None = None


@app.post("/api/lazy/tick")
def lazy_tick_api(body: TickIn, request: Request, x_bento_code: str | None = Header(default=None)):
    """앱을 열 때마다 부른다. 팀이 켠 규칙과 시간 조건이 맞을 때만 움직인다.

    GitHub에 댓글을 달거나 실행하므로 로그인(또는 팀 접근 코드)이 있어야 한다. 화면은 실패하면 조용히 넘어간다.
    """
    _check_code(x_bento_code, request)
    return lazy_tick(body.repo)


_LAST_ALERT: dict[str, str] = {}


NOTICE_BASE = "bento-notice-base"  # 지난번에 본 공고 {해시|신청 마감|제출 마감}를 팀 저장소 라벨 설명에 둔다 (서버가 다시 켜져도 이어서 비교)


def _read_notice_base(gh) -> list[str] | None:
    for lb in gh.labels():
        if lb.get("name") == NOTICE_BASE:
            parts = (lb.get("description") or "").split("|")
            return parts if len(parts) == 3 else None
    return None


def _write_notice_base(gh, h: str, a: str | None, s: str | None) -> None:
    desc = f"{h}|{a or ''}|{s or ''}"
    try:
        gh.update_label(NOTICE_BASE, desc)
    except GitHubError:
        gh.create_label(NOTICE_BASE, "C5DEF5", desc)


def _kst_text(iso: str) -> str:
    d = datetime.fromisoformat(iso).astimezone(timezone(timedelta(hours=9)))
    return f"{d.month}월 {d.day}일({'월화수목금토일'[d.weekday()]}) {d.hour:02d}:{d.minute:02d}"


def notice_watch(gh, st, hook: str) -> str:
    """공고가 바뀌었는지 하루 한 번 서버가 다시 읽는다 (규칙으로만, AI 없음). 마감이 바뀌었을 때만 팀 디스코드로 알린다.

    처음 한 번은 기준만 저장하고, 본문이 바뀌어도 마감이 그대로면 기준만 고친다 (조회수·댓글 같은 변화는 알리지 않는다).
    """
    src = notice_mod.source_url([m.description for m in st.milestones])
    if not src:
        return "no-source"
    try:
        page = fetch_notice(src)
    except FetchError:
        return "unreadable"
    text = notice_mod.clean_text(page["text"])
    h = notice_mod.notice_hash(text)
    from .rules import rule_extract
    try:
        ex = rule_extract(text)
        a, s_iso = ex.apply_deadline_iso, ex.submit_deadline_iso
    except Exception:  # noqa: BLE001 - 날짜를 못 읽으면 비교하지 않는다
        return "unreadable"
    base = _read_notice_base(gh)
    if not base:
        _write_notice_base(gh, h, a, s_iso)
        return "first"
    if base[0] == h:
        return "same"
    diffs = [(label, old, new) for label, old, new in (("신청 마감", base[1], a), ("제출 마감", base[2], s_iso)) if old and new and old != new]
    if diffs:
        import httpx
        app_url = os.getenv("BENTO_APP_URL", "")
        lines = [f"[{gh.repo}] 공고가 바뀌었어요"] + [f"· {label}: {_kst_text(old)} → {_kst_text(new)}" for label, old, new in diffs]
        lines.append("→ 앱을 열면 '바뀐 날짜로 고치기'가 나와요. 공고에서 직접 확인한 뒤 계획 날짜를 고쳐 주세요." + (f"\n{app_url}" if app_url else ""))
        lines.append(f"공고 다시 보기: {src}")
        httpx.post(hook, json={"content": "\n".join(lines), "allowed_mentions": {"parse": []}}, timeout=10)
    _write_notice_base(gh, h, a, s_iso)
    return "changed" if diffs else "text-only"


@app.get("/api/agent/run")
def agent_run(authorization: str | None = Header(default=None)):
    """매일 정해진 시간에 혼자 도는 점검 (Vercel Cron 등). 저장소는 읽기만 하고, 발견한 게 있으면 팀 채널에 알린다.

    고치는 행동은 하지 않는다. 다시 짠 계획은 앱에서 사람이 승인해야 실행된다.
    """
    secret = os.getenv("CRON_SECRET")
    if not secret or not hmac.compare_digest(authorization or "", f"Bearer {secret}"):
        raise HTTPException(401, "예약 실행 비밀값이 맞지 않아요")
    repos = installed_repos(settings) if settings.live else [_demo.repo]
    report = []
    for repo in repos:
        try:
            gh, st = state_for(repo)
        except HTTPException:
            continue
        ex = notice_mod.from_descriptions([m.description for m in st.milestones])
        r = prog.check(st, [], ex.submit_deadline_iso if ex else None)
        sent = False
        team_hook = discord_mod.read(gh, settings.signing_key)
        hook = team_hook or settings.discord_webhook
        notify = bool(team_hook) or os.getenv("BENTO_AUTO_NOTIFY") == "1"  # 팀이 디스코드를 직접 연결했으면 알림을 받겠다는 뜻이다
        key = hashlib.sha256(r["summary"].encode()).hexdigest()[:12]
        rules_now = getattr(st, "rules", {}) or {}
        if rules_now.get("seed") and hook and notify:  # 당번 순서를 정해 둔 팀만: 새 당번이 시작하는 날 알린다
            sched = own.duty_schedule(st.members, rules_now, datetime.now(timezone.utc), 2)
            today_kst = datetime.now(own.KST).date().isoformat()
            if len(sched) == 2 and sched[0]["from"] == today_kst and _LAST_ALERT.get("duty:" + repo) != today_kst:
                import httpx
                days = rules_now.get("rotate", 1)
                httpx.post(hook, json={"content": f"[{repo}] 오늘부터 {days}일 동안 당번은 {sched[0]['who']}님이에요 (~{sched[0]['to'][5:].replace('-', '/')}). 다음 당번은 {sched[1]['who']}님, {sched[1]['from'][5:].replace('-', '/')}부터예요.",
                                           "allowed_mentions": {"parse": []}}, timeout=10)
                _LAST_ALERT["duty:" + repo] = today_kst
        if r["findings"] and hook and notify and _LAST_ALERT.get(repo) != key:
            import httpx
            app_url = os.getenv("BENTO_APP_URL", "")
            httpx.post(hook, json={"content": f"[{repo}]\n{r['summary']}" + (f"\n앱에서 확인하고 승인하기: {app_url}" if app_url else ""),
                                                       "allowed_mentions": {"parse": []}}, timeout=10)
            _LAST_ALERT[repo] = key
            sent = True
        # 승인이 미뤄지면 단계적으로 알린다: 12시간 → 주인에게 다시, 24시간 → 팀 전체
        lines = []
        for it in triage(st, settings.review_wait_hours):
            owner, age = own.owner_and_age(it, st, settings.review_wait_hours, datetime.now(timezone.utc))
            lv = own.level(age)
            k = f"{repo}:{it['id']}:{lv}"
            if lv and _LAST_ALERT.get(k) != "sent":
                _LAST_ALERT[k] = "sent"
                lines.append(f"· {owner}님이 처리할 차례예요: {it['title']} ({prog.span(age)}째 그대로)" if lv == 1
                             else f"· {it['title']} ({prog.span(age)}째 그대로) · 이제 팀원 누구나 처리할 수 있어요")
        if lines and hook and notify:
            import httpx
            app_url = os.getenv("BENTO_APP_URL", "")
            httpx.post(hook, json={"content": f"[{repo}] 처리를 기다리는 일\n" + "\n".join(lines)
                                   + "\n→ Bento가 해결 방법을 제안해 뒀어요. 앱 홈에서 제안을 확인하고 '승인하고 실행'을 누르면 바로 처리돼요."
                                   + (f"\n{app_url}" if app_url else ""), "allowed_mentions": {"parse": []}}, timeout=10)
        notice_result = None
        if hook and notify:
            try:
                notice_result = notice_watch(gh, st, hook)
            except Exception:  # noqa: BLE001 - 공고 사이트가 잠깐 안 열려도 다른 점검은 계속한다
                notice_result = "error"
        tick = lazy_tick(repo)
        # 새로 올라온 대회 알리기 (마감 2주 안, 처음 본 것만)
        try:
            fresh = [x for x in disc.discover()["items"] if x["deadline_iso"] and _LAST_ALERT.get("disc:" + x["id"]) != "sent"
                     and datetime.fromisoformat(x["deadline_iso"]) - datetime.now(timezone.utc) < timedelta(days=14)][:3]
        except Exception:  # noqa: BLE001 - 출처 사이트가 잠깐 안 열려도 다른 점검은 계속한다
            fresh = []
        for x in fresh:
            _LAST_ALERT["disc:" + x["id"]] = "sent"
        if fresh and hook and os.getenv("BENTO_AUTO_NOTIFY") == "1":
            import httpx
            httpx.post(hook, json={"content": "Bento가 찾아온 대회\n" + "\n".join(f"· {x['title']} · 마감 {x['deadline_text']} ({x['source_name']})" for x in fresh),
                                                       "allowed_mentions": {"parse": []}}, timeout=10)
        report.append({"repo": repo, "findings": len(r["findings"]), "notified": sent, "escalations": len(lines), "lazy": tick, "notice": notice_result})
    return {"ok": True, "report": report}


@app.get("/api/discover")
def discover_api(request: Request, interests: str = "", force: bool = False, x_bento_code: str | None = Header(default=None)):
    """Bento가 먼저 찾아온 대회. 학과 게시판·공개 행사 목록을 1시간에 한 번만 읽는다. AI를 쓰지 않는다.

    force(1시간을 기다리지 않고 다시 읽기)는 출처 사이트를 직접 읽으러 가므로 로그인이나 팀 접근 코드가 있어야 한다.
    """
    if force:
        _check_code(x_bento_code, request)
    return disc.discover([k.strip() for k in interests.split(",") if k.strip()][:10], force=force)


@app.get("/api/poster")
def poster_api(id: str):
    """찾아온 대회의 포스터 이미지 주소 (출처 사이트의 이미지를 그대로 가리킨다)."""
    return disc.poster(id)


@app.get("/api/notice/saved")
def saved_notice(repo: str | None = None):
    """팀원이 이미 분석해서 저장소에 저장해 둔 공고. AI를 부르지 않는다."""
    gh, st = state_for(repo)
    ex = notice_mod.from_descriptions([m.description for m in st.milestones])
    return {"extract": ex.model_dump() if ex else None}


@app.get("/api/usage")
def usage():
    """이 서버가 켜진 뒤의 AI 사용량. 서버리스에서는 인스턴스마다 따로 센다."""
    u = notice_mod.USAGE
    calls = u["ai_calls"]
    return {**u, "avg_usd_per_call": round(u["est_usd"] / calls, 5) if calls else None,
            "note": "추정치예요. 정확한 비용은 Claude Console 사용량에서 확인하세요."}


class ExecuteIn(BaseModel):
    token: str
    edits: dict[int, str] = {}
    skip: list[int] = []  # 승인 화면에서 체크를 푼 행동 번호


def _run(repo: str, actions: list, by: str | None = None, require_member: bool = False, proposer: str | None = None) -> list[dict]:
    """승인된 행동을 안전하게 실행한다 (사람 승인 · 반대가 없으면 실행 모두 이 길로)."""
    data = {"repo": repo}
    # 팀원 여럿이 같은 제안을 동시에 승인해도 한 번만, 그리고 제안할 때 본 상태 그대로일 때만 실행한다.
    key = hashlib.sha256(json.dumps([data["repo"], [a.model_dump() for a in actions]], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    already = HTTPException(409, {"message": "다른 팀원이 같은 일을 이미 승인해서 실행했어요", "errors": [], "stale": True})
    with _repo_lock(data["repo"]):  # 같은 서버 안: 한 번에 하나씩
        if (done := _EXECUTED.get(key)) and time.time() - done < 6 * 3600:
            raise already
        gh = gh_for(data["repo"])
        try:
            coord.acquire(gh)  # 서버 여러 대 사이: GitHub 라벨로 잠근다
        except coord.Busy:
            raise HTTPException(409, {"message": "다른 팀원의 승인을 실행하는 중이에요. 몇 초 뒤에 다시 눌러 주세요", "errors": [], "busy": True}) from None
        except GitHubError as e:
            raise HTTPException(502 if e.status != 404 else 404, f"GitHub에서 잠금을 걸지 못했어요: {e}") from e
        try:
            if key[:10] in coord.recent(gh):  # 다른 서버에서 이미 실행한 제안
                raise already
            gh, st = state_for(data["repo"])  # 잠근 뒤에 저장소를 다시 읽는다
            errors = validate(actions, st)
            if errors:
                stale = any(e.split(": ", 1)[-1].startswith(STALE) for e in errors)
                raise HTTPException(409, {"message": "다른 팀원이 먼저 처리해서 실행하지 않았어요. 새 상태로 다시 볼게요" if stale
                                          else "저장소 상태가 바뀌었거나 실행할 수 없는 행동이 있어서 실행하지 않았어요", "errors": errors, "stale": stale})
            if by and by != "lazy" and by not in st.members:
                if require_member and settings.live:
                    raise HTTPException(403, {"message": f"{by}님은 {gh.repo} 팀원이 아니라서 실행할 수 없어요", "errors": []})
                by = None  # 팀원 목록에 없는 이름은 적지 않는다
            results = execute(actions, gh, settings, op=key[:12], by=f"{proposer}|{by}" if proposer in st.members and by and by != "lazy" else by)  # 팀원 이름만 적는다
            if any(r["ok"] for r in results):
                _EXECUTED[key] = time.time()
                coord.mark_done(gh, key)
        finally:
            coord.release(gh)
    return results


@app.post("/api/execute")
def run_proposal(body: ExecuteIn, request: Request, x_bento_code: str | None = Header(default=None), x_bento_user: str | None = Header(default=None)):
    user = current_user(request)
    if settings.login_enabled:
        if not user:  # 로그인을 켜면 누가 승인했는지 GitHub가 확인한 사람만 실행한다
            raise HTTPException(401, {"message": "GitHub로 로그인해야 실행할 수 있어요", "login": True})
        x_bento_user = user["login"]
    else:
        _check_code(x_bento_code)
    try:
        data = verify(body.token, settings.signing_key)
    except ProposalError as e:
        raise HTTPException(400, str(e)) from e
    actions = ActionList.validate_python(data["actions"])
    for idx, text in body.edits.items():  # 사람이 고칠 수 있는 건 글뿐
        if 0 <= idx < len(actions) and (field := EDITABLE.get(actions[idx].type)):
            actions[idx] = actions[idx].model_copy(update={field: text})
    ran = [i for i in range(len(actions)) if i not in set(body.skip)]  # 빼는 것만 가능하고, 더하거나 바꿀 수는 없다
    actions = [actions[i] for i in ran]
    if not actions:
        raise HTTPException(400, "실행할 행동을 하나 이상 골라 주세요")
    who = (x_bento_user or "").strip()
    if any(a.type in TEAM_WIDE for a in actions) and data.get("by") and who and data["by"] == who:
        gh, st = state_for(data["repo"])
        if len(st.members) > 1 and who not in st.leads:
            raise HTTPException(409, {"message": "팀 전체에 영향을 주는 변경이라, 제안한 사람이 아닌 팀원이나 팀장이 승인해야 해요", "need_second": True,
                                      "leads": st.leads})
    results = _run(data["repo"], actions, by=who or None, require_member=settings.login_enabled, proposer=data.get("by"))
    from . import signals as _sig
    _sig._CACHE.clear()  # 방금 실행한 일이 다음 목록에 바로 반영되게 (같은 제안을 두 번 누르지 않게)
    if all(r["ok"] for r in results):
        _close_request(data["repo"], body.token, who)
    for i, r in zip(ran, results):
        r["index"] = i
    return {"ok": all(r["ok"] for r in results), "results": results}


def _tok_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _close_request(repo: str, token: str, who: str | None) -> None:
    """승인 요청 이슈로 들어온 제안이 실행되면 그 이슈를 닫는다."""
    try:
        gh = gh_for(repo)
        key = _tok_key(token)
        for i in gh.open_issues():
            if (i.get("title") or "").startswith(APPROVE_PREFIX) and f"bento-approve:{key}" in (i.get("body") or ""):
                gh.comment(i["number"], f"승인: @{who} · Bento가 실행했어요." if who else "승인돼서 Bento가 실행했어요.")
                gh.update_issue(i["number"], state="closed")
    except Exception:  # noqa: BLE001 - 닫지 못해도 실행은 끝났다
        pass


class RequestIn(BaseModel):
    token: str


@app.post("/api/approval-request")
def approval_request(body: RequestIn, request: Request, x_bento_code: str | None = Header(default=None)):
    """다른 팀원(또는 팀장)에게 승인 요청 보내기: GitHub에 요청 이슈를 만들고, 팀원 Bento의 승인 대기에 뜬다."""
    _check_code(x_bento_code, request)
    try:
        data = verify(body.token, settings.signing_key)
    except ProposalError as e:
        raise HTTPException(400, str(e)) from e
    gh, st = state_for(data["repo"])
    # 로그인을 켠 서버: 그 저장소 팀원이, 자기가 낸 제안만 승인 요청을 보낼 수 있다 (팀 밖에서 이슈를 만들거나 팀원을 사칭하지 못하게)
    actor = _member_actor(request, st, "승인 요청을 보낼")
    if actor and data.get("by") != actor:
        raise HTTPException(403, "내가 낸 제안만 승인 요청을 보낼 수 있어요. 화면을 새로 고친 뒤 다시 제안해 주세요")
    actions = ActionList.validate_python(data["actions"])
    key = _tok_key(body.token)
    for i in st.issues:
        if i.title.startswith(APPROVE_PREFIX) and f"bento-approve:{key}" in (i.body or ""):
            return {"issue": i.number, "url": i.url, "already": True}
    by = data.get("by") or "팀원"
    lines = "\n".join(f"- {s['label']}" + (f" · {s['sub']}" if s["sub"] else "") for s in _steps(actions, gh.repo))
    ask = " ".join(f"@{m}" for m in st.leads if m != data.get("by")) or "다른 팀원"
    text = (f"**{by}** 님이 제안했어요. {ask} 님(또는 다른 팀원)이 Bento에서 확인하고 승인해 주세요.\n\n{lines}\n\n"
            "팀 전체에 영향을 주는 변경이라, 제안한 사람이 아닌 팀원이나 팀장이 승인해야 실행돼요.\n"
            f"<!-- bento-approve:{key} -->\n<!-- bento-token:{body.token} -->")
    n = gh.create_issue(f"{APPROVE_PREFIX} {data.get('summary', '')[:60]}", text, [], None)
    return {"issue": n.get("number"), "url": n.get("html_url"), "already": False}


def _approval_items(gh, st) -> list[dict]:
    """GitHub에 올라온 승인 요청 → 승인 대기 항목 (제안한 사람 말고 다른 팀원·팀장이 승인)."""
    out = []
    for i in st.issues:
        if not i.title.startswith(APPROVE_PREFIX):
            continue
        try:
            body = gh.issue(i.number).get("body") or ""
            m = re.search(r"bento-token:([A-Za-z0-9._\-=]+)", body)
            data = verify(m.group(1), settings.signing_key) if m else None
        except (ProposalError, GitHubError, AttributeError):
            data = None
        if not data or data.get("repo") != gh.repo:
            continue
        actions = ActionList.validate_python(data["actions"])
        out.append({"id": f"approve-{i.number}", "sev": "주의", "title": f"승인 요청 · {data.get('summary', '')}",
                    "why": f"{data.get('by') or '팀원'} 님이 제안했어요 · 제안한 사람이 아닌 팀원이나 팀장이 승인해요",
                    "evidence": [{"label": f"#{i.number} 승인 요청", "url": i.url}], "options": [],
                    "proposal": {"summary": data.get("summary", ""), "steps": _steps(actions, gh.repo), "token": m.group(1), "by": data.get("by"), "team_wide": True},
                    "proposed_by": data.get("by")})
    return out


class SubmitIn(BaseModel):
    repo: str | None = None
    deliverables: list[notice_mod.Deliverable]
    deadline_iso: str | None = None
    links: dict[str, str] = {}


@app.post("/api/submit-check")
def submit_check(body: SubmitIn, request: Request, x_bento_code: str | None = Header(default=None)):
    """제출 직전 확인: 자동 확인(근거 포함) · 사람 확인 목록 · 팀원 확인 상태.

    사용자가 적은 링크는 서버가 대신 열어 보므로, 링크가 있을 때만 로그인이나 팀 접근 코드를 확인한다
    (공고 링크 열기와 같은 기준. 링크 없이 저장소만 보는 점검은 그대로).
    """
    links = {k: v.strip() for k, v in body.links.items() if k in ("deploy", "video", "slides") and v and v.strip().startswith(("http://", "https://"))}
    if links:
        _check_code(x_bento_code, request)
    gh, st = state_for(body.repo)
    try:
        out = sub_mod.run_checks(gh, st, body.deliverables, body.deadline_iso, links)
    except GitHubError as e:
        raise HTTPException(502, f"GitHub에서 읽지 못했어요: {e}") from e
    n = sub_mod.find_confirm_issue(st)
    out["confirm"] = sub_mod.confirm_status(gh, n, st.members) if n else None
    out["members"] = st.members
    return out


class ApplyCheckIn(BaseModel):
    extract: notice_mod.NoticeExtract
    members: list[str] = []
    answers: dict = {}
    source_url: str | None = None


class EligibilityIn(BaseModel):
    extract: notice_mod.NoticeExtract
    answers: dict = {}
    source_url: str | None = None


@app.post("/api/eligibility")
def eligibility(body: EligibilityIn):
    """이 공고에 필요한 질문과, 지금 답으로 본 조건별 판정 (AI 없이 규칙으로)."""
    from . import eligibility as el
    return {"questions": el.questions(body.extract), "rows": el.judge(body.extract, body.answers, body.source_url)}


@app.post("/api/apply-check")
def apply_check(body: ApplyCheckIn):
    """신청 직전 확인. 저장소가 없어도 된다."""
    from datetime import datetime, timezone
    from . import eligibility as el
    ex = body.extract
    answers = dict(body.answers)
    if not answers.get("team"):
        answers["team"] = len([m for m in body.members if m.strip()]) or ""
    rows = el.judge(ex, answers, body.source_url)
    if ex.apply_deadline_iso:
        left = (datetime.fromisoformat(ex.apply_deadline_iso) - datetime.now(timezone.utc)).total_seconds() / 3600
        rows.append(sub_mod._row("신청 마감까지", "fail" if left <= 0 else "warn" if left < 3 else "ok",
                                 "마감이 지났어요" if left <= 0 else f"{int(left // 24)}일 {int(left % 24)}시간 남았어요",
                                 [{"label": f"공고: {f.quote}"} for f in ex.facts if f.label == "신청 마감"][:1]))
    missing = [f for f in ex.apply_fields]
    rows.append(sub_mod._row("신청서 항목", "ok" if missing else "warn", f"{len(missing)}개 · {', '.join(missing)}" if missing else "공고에서 신청 항목을 찾지 못했어요. 폼을 직접 확인해 주세요"))
    return {"rows": rows, "human": sub_mod.human_checks([], "apply"), "ready": not any(r["status"] == "fail" for r in rows)}


class ConfirmIn(BaseModel):
    repo: str | None = None
    title: str
    head_sha: str
    checklist: list[str] = []


@app.post("/api/submit/confirm")
def submit_confirm(body: ConfirmIn):
    """다른 팀원에게 확인을 받는 이슈를 만드는 제안. 지금 커밋을 '승인한 버전'으로 고정한다."""
    gh, st = state_for(body.repo)
    if not re.fullmatch(r"[0-9a-f]{7,40}", body.head_sha):
        raise HTTPException(400, "커밋 정보가 올바르지 않아요")
    actions = []
    if (old := sub_mod.find_confirm_issue(st)):
        actions.append(CloseIssue(issue=old, comment=f"새 버전({body.head_sha[:7]})으로 다시 확인을 요청해서 이 요청은 닫아요."))
    checks = "\n".join(f"- [x] {c}" for c in body.checklist[:10])
    text = (f"제출 전에 다른 팀원 한 명이 확인해 주세요.\n\n**확인할 버전**: {gh.html('/commit/' + body.head_sha)}\n\n"
            f"요청한 사람이 확인한 것:\n{checks or '- (없음)'}\n\n"
            "괜찮으면 이 이슈에 👍를 누르거나 '확인' 댓글을 남겨 주세요. 이 버전 뒤에 커밋이 생기면 Bento가 다시 알려요.\n\n"
            f"<!-- bento-lock:{body.head_sha} -->")
    title = f"{sub_mod.CONFIRM_PREFIX} {body.title.strip()[:50]} · {body.head_sha[:7]}"
    actions.append(CreateIssue(title=title, body=text))
    actions.append(TeamMessage(text=f"제출 확인 부탁해요! {body.title.strip()[:50]} · 버전 {body.head_sha[:7]} · GitHub 이슈 '{title}'에 👍 눌러 주세요"))
    return {"proposal": _proposal(gh.repo, "다른 팀원에게 제출 확인 받기", actions, "confirm")}


@app.get("/api/submit/status")
def submit_status(repo: str | None = None, issue: int | None = None):
    gh, st = state_for(repo)
    n = issue or sub_mod.find_confirm_issue(st)
    if not n:
        return {"issue": None}
    try:
        out = sub_mod.confirm_status(gh, n, st.members)
    except GitHubError as e:
        raise HTTPException(404, "확인 요청 이슈를 찾지 못했어요") from e
    try:
        head = gh.head_sha(gh.repo_info().get("default_branch") or "main")
        out["changed"] = bool(out["lock"]) and not head.startswith(out["lock"])
    except GitHubError:
        out["changed"] = None
    return out


class ProofIn(BaseModel):
    repo: str | None = None
    issue: int
    proof: str


@app.post("/api/submit/proof")
def submit_proof(body: ProofIn):
    """제출 증거(접수 번호·확인 메일 문구)를 남기고 확인 이슈를 닫는 제안."""
    gh, st = state_for(body.repo)
    proof = " ".join(body.proof.split())[:300]
    if len(proof) < 2:
        raise HTTPException(400, "접수 번호나 확인 메일 문구를 적어 주세요")
    lock = sub_mod.confirm_status(gh, body.issue, st.members)["lock"] if st.issue(body.issue) else None
    comment = f"제출 완료 증거: {proof}\n제출 버전: {lock[:7] if lock else '기록 없음'} · 제출은 팀원이 직접 했어요"
    return {"proposal": _proposal(gh.repo, "제출 완료 기록하기", [CloseIssue(issue=body.issue, comment=comment)], "proof")}


class FormIn(BaseModel):
    url: str
    values: dict[str, str] = {}


@app.post("/api/form/inspect")
def form_inspect(body: FormIn, request: Request, x_bento_code: str | None = Header(default=None)):
    """신청·제출 폼을 읽어 미리 채운 링크를 만든다. 제출 버튼은 사람이 누른다."""
    _check_code(x_bento_code, request)
    values = {k: v.strip()[:500] for k, v in body.values.items() if isinstance(v, str) and v.strip()}
    try:
        return inspect_form(body.url.strip(), values)
    except (FormError, FetchError) as e:
        raise HTTPException(422, str(e)) from e
    except (ValueError, IndexError, KeyError, TypeError) as e:
        raise HTTPException(422, "폼 구조를 읽지 못했어요. 칸마다 복사해서 넣어 주세요") from e


@app.post("/api/github/webhook")
async def webhook(request: Request, x_hub_signature_256: str | None = Header(default=None), x_github_event: str | None = Header(default=None)):
    raw = await request.body()
    if not settings.webhook_secret:
        raise HTTPException(503, "웹훅 비밀값이 설정되지 않았어요")
    good = "sha256=" + hmac.new(settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, x_hub_signature_256 or ""):
        raise HTTPException(401, "웹훅 서명이 맞지 않아요")
    # 지금은 상태를 저장하지 않고, 화면이 열릴 때 저장소를 새로 읽는다. 알림 전송은 다음 단계.
    return {"ok": True, "event": x_github_event}


@app.post("/api/demo/reset")
def demo_reset():
    if settings.live:
        raise HTTPException(400, "실제 저장소 모드에서는 쓸 수 없어요")
    _demo.reset()
    _EXECUTED.clear()
    return {"ok": True}


@app.get("/")
def index():
    """로컬 실행용. Vercel에서는 public/index.html을 바로 내보낸다."""
    return FileResponse("public/index.html", headers={"Cache-Control": "no-cache"})  # 고친 화면이 바로 보이게


@app.get("/manifest.webmanifest")
def manifest():
    """로컬 실행용. Vercel에서는 public/manifest.webmanifest를 바로 내보낸다."""
    return FileResponse("public/manifest.webmanifest", media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@app.get("/icons/{name}")
def app_icon(name: str):
    """로컬 실행용. Vercel에서는 public/icons/를 바로 내보낸다."""
    path = os.path.join("public", "icons", os.path.basename(name))
    if not name.endswith(".png") or not os.path.isfile(path):
        raise HTTPException(404, "없는 아이콘이에요")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/design/")
@app.get("/design/{name}")
def design_page(name: str = "index.html"):
    """피그마 가져오기용 시안 (로컬 실행용). Vercel에서는 public/design/을 바로 내보낸다."""
    path = os.path.join("public", "design", os.path.basename(name))
    if not name.endswith(".html") or not os.path.isfile(path):
        raise HTTPException(404, "없는 시안이에요")
    return FileResponse(path, headers={"Cache-Control": "no-cache"})
