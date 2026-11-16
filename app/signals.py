"""PR·이슈 말고 저장소에서 읽을 수 있는 팀 신호를 모은다. 모두 읽기만 한다.

- 기본 브랜치(main) 빌드 결과, 최근 커밋(계정 연결 여부·마감 뒤 커밋), 팀원별 마지막 활동
- 저장소 파일 목록(큰 파일·올리면 안 되는 폴더·비밀키 파일), 최근 커밋에 들어간 키 모양
- 수락하지 않은 초대, 리뷰 승인 없이 머지된 PR, 수정 요청 뒤 새 커밋이 있는지

권한이 없거나 읽지 못한 항목은 None으로 두고 넘어간다. 그 항목에 대한 경고만 만들지 않는다.
"""

import re
import time
from datetime import datetime, timedelta

from .state import parse_ts

_CACHE: dict[str, tuple[float, dict]] = {}
TTL = 120  # 홈·대화가 연달아 불러도 GitHub에 같은 요청을 반복하지 않게

SECRET_FILE = re.compile(r"(^|/)(\.env(\.[\w-]+)?|[^/]*\.pem|id_rsa|id_ed25519|[^/]*\.p12|[^/]*\.pfx|credentials\.json|service[-_]?account[^/]*\.json|firebase-adminsdk[^/]*\.json)$", re.I)
SECRET_SAFE = re.compile(r"\.(example|sample|template|dist|defaults?)$", re.I)
# 값은 절대 화면에 내보내지 않는다. 어떤 종류의 키인지만 알린다.
KEY_SHAPES = [
    ("AWS 키", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub 토큰", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,})")),
    ("Anthropic API 키", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI API 키", re.compile(r"\bsk-(proj-)?[A-Za-z0-9]{32,}")),
    ("Google API 키", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("Slack 토큰", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("개인 키", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
]
JUNK_DIR = re.compile(r"(^|/)(node_modules|\.venv|venv|__pycache__|\.next|\.gradle|Pods)/")
BIG_BYTES = 20 * 1024 * 1024


def secret_file(path: str) -> bool:
    return bool(SECRET_FILE.search(path)) and not SECRET_SAFE.search(path)


def key_kinds(patch: str) -> list[str]:
    added = "\n".join(l[1:] for l in (patch or "").splitlines() if l.startswith("+") and not l.startswith("+++"))
    return [name for name, rx in KEY_SHAPES if rx.search(added)]


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - 권한이 없거나 잠깐 못 읽으면 그 신호만 빼고 간다
        return default


def collect(gh, prs, now: datetime) -> dict:
    """gh: GitHub 또는 DemoGitHub. prs: 이미 읽은 열린 PR 목록(state.PR)."""
    key = gh.repo
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < TTL and not getattr(gh, "no_cache", False):
        return hit[1]
    info = _safe(gh.repo_info, {}) or {}
    branch = info.get("default_branch") or "main"
    sig: dict = {"branch": branch, "main": None, "commits": None, "activity": None, "invites": None,
                 "tree": None, "keys": [], "merged_unreviewed": None, "changes": {}}

    # 1) main 빌드
    sha = _safe(lambda: gh.head_sha(branch))
    if sha:
        from .state import ci_status
        runs = _safe(lambda: gh.check_runs(sha), []) or []
        ci, fail_h = ci_status(runs, now)
        sig["main"] = {"sha": sha, "ci": ci, "fail_hours": fail_h}

    # 2) 최근 2주 커밋 (main)
    raw = _safe(lambda: gh.commits(branch, (now - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")), None)
    if raw is not None:
        sig["commits"] = [{"sha": c["sha"], "login": (c.get("author") or {}).get("login"),
                           "name": ((c.get("commit") or {}).get("author") or {}).get("name") or "",
                           "at": ((c.get("commit") or {}).get("committer") or {}).get("date"),
                           "message": ((c.get("commit") or {}).get("message") or "").splitlines()[0][:80] if (c.get("commit") or {}).get("message") else "",
                           "url": c.get("html_url") or gh.html(f"/commit/{c['sha']}")} for c in raw]
        if sig["main"] and sig["commits"]:
            head = next((c for c in sig["commits"] if c["sha"] == sha), sig["commits"][0])
            sig["main"].update(author=head["login"], message=head["message"], url=head["url"])
        # 최근 커밋 5개에 들어간 키 모양 (추가된 줄만 본다)
        for c in sig["commits"][:5]:
            detail = _safe(lambda c=c: gh.commit(c["sha"]), {}) or {}
            for f in detail.get("files", []) or []:
                kinds = key_kinds(f.get("patch") or "")
                if kinds:
                    sig["keys"].append({"path": f.get("filename"), "kinds": kinds, "sha": c["sha"], "login": c["login"], "name": c["name"], "url": c["url"]})

    # 3) 팀원별 마지막 활동 (푸시·PR·리뷰·댓글·이슈, 어느 브랜치든)
    ev = _safe(gh.events, None)
    if ev is not None:
        last: dict[str, str] = {}
        for e in ev:
            who, at = (e.get("actor") or {}).get("login"), e.get("created_at")
            if who and at and at > last.get(who, ""):
                last[who] = at
        for c in sig["commits"] or []:
            if c["login"] and c["at"] and c["at"] > last.get(c["login"], ""):
                last[c["login"]] = c["at"]
        sig["activity"] = last
        sig["pushes"] = [{"login": (e.get("actor") or {}).get("login"), "at": e.get("created_at"), "ref": (e.get("payload") or {}).get("ref", "")}
                         for e in ev if e.get("type") == "PushEvent"]

    # 4) 수락하지 않은 초대 (저장소 관리 권한이 없으면 못 읽는다)
    inv = _safe(gh.invitations, None)
    if inv is not None:
        sig["invites"] = [{"login": (i.get("invitee") or {}).get("login") or "(이메일 초대)", "at": i.get("created_at"), "expired": bool(i.get("expired"))} for i in inv]

    # 5) 파일 목록: 큰 파일 · 올리면 안 되는 폴더 · 비밀키 파일
    if sha:
        blobs = _safe(lambda: gh.tree(sha), None)
        if blobs is not None:
            sig["tree"] = {"big": [{"path": b["path"], "size": b.get("size", 0)} for b in blobs if (b.get("size") or 0) >= BIG_BYTES][:5],
                           "junk": sorted({JUNK_DIR.search(b["path"]).group(0).strip("/").split("/")[-1] for b in blobs if JUNK_DIR.search(b["path"])}),
                           "secrets": [b["path"] for b in blobs if secret_file(b["path"])][:5]}

    # 6) 최근 3일 안에 리뷰 승인 없이 머지된 PR
    merged = _safe(gh.merged_prs, None)
    if merged is not None:
        out = []
        for m in merged[:8]:
            at = parse_ts(m.get("merged_at"))
            if not at or now - at > timedelta(days=3):
                continue
            author = (m.get("user") or {}).get("login")
            revs = _safe(lambda m=m: gh.pr_reviews(m["number"]), []) or []
            if not any(r.get("state") == "APPROVED" and (r.get("user") or {}).get("login") != author for r in revs):
                out.append({"number": m["number"], "title": m.get("title", ""), "author": author, "url": m.get("html_url") or gh.html(f"/pull/{m['number']}"), "merged_at": m.get("merged_at")})
        sig["merged_unreviewed"] = out

    # 7) 수정 요청을 받은 PR: 그 뒤에 새 커밋이 있었는지
    for p in prs:
        if not p.changes_at:
            continue
        cs = _safe(lambda p=p: gh.pr_commits(p.number), None)
        if cs is None:
            continue
        last = max((((c.get("commit") or {}).get("committer") or {}).get("date") or "" for c in cs), default="")
        sig["changes"][p.number] = last

    _CACHE[key] = (time.time(), sig)
    return sig


def clear() -> None:
    _CACHE.clear()
