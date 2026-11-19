"""PR·이슈 말고 저장소에서 읽을 수 있는 팀 신호를 모은다. 모두 읽기만 한다.

- 기본 브랜치(main) 빌드 결과, 최근 커밋(계정 연결 여부·마감 뒤 커밋), 팀원별 마지막 활동
- 저장소 파일 목록(큰 파일·올리면 안 되는 폴더·비밀키 파일), 최근 커밋에 들어간 키 모양
- 수락하지 않은 초대, 리뷰 승인 없이 머지된 PR, 수정 요청 뒤 새 커밋이 있는지

권한이 없거나 읽지 못한 항목은 None으로 두고 넘어간다. 그 항목에 대한 경고만 만들지 않는다.
"""

import re
import time
from datetime import datetime, timedelta

from .state import par, parse_ts

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
    """gh: GitHub 또는 DemoGitHub. prs: 이미 읽은 열린 PR 목록(state.PR). 서로 기다릴 필요 없는 읽기는 동시에 한다."""
    key = gh.repo
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < TTL and not getattr(gh, "no_cache", False):
        return hit[1]
    info = _safe(gh.repo_info, {}) or {}
    branch = info.get("default_branch") or "main"
    sig: dict = {"branch": branch, "main": None, "commits": None, "activity": None, "invites": None,
                 "tree": None, "keys": [], "merged_unreviewed": None, "changes": {}}
    since = (now - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1단계: 브랜치 이름만 알면 되는 읽기들
    sha, raw, ev, inv, merged = par([
        lambda: _safe(lambda: gh.head_sha(branch)),
        lambda: _safe(lambda: gh.commits(branch, since), None),
        lambda: _safe(gh.events, None),
        lambda: _safe(gh.invitations, None),
        lambda: _safe(gh.merged_prs, None),
    ])
    commits = None
    if raw is not None:
        commits = [{"sha": c["sha"], "login": (c.get("author") or {}).get("login"),
                    "name": ((c.get("commit") or {}).get("author") or {}).get("name") or "",
                    "at": ((c.get("commit") or {}).get("committer") or {}).get("date"),
                    "message": ((c.get("commit") or {}).get("message") or "").splitlines()[0][:80] if (c.get("commit") or {}).get("message") else "",
                    "url": c.get("html_url") or gh.html(f"/commit/{c['sha']}")} for c in raw]
    sig["commits"] = commits

    # 최근 3일 안에 머지된 PR 후보 (리뷰 승인 없이 머지됐는지는 아래에서 한꺼번에 읽는다)
    cand = []
    if merged is not None:
        for m in merged[:8]:
            at = parse_ts(m.get("merged_at"))
            if at and now - at <= timedelta(days=3):
                cand.append(m)

    # 2단계: 위에서 얻은 값이 필요한 읽기들을 한꺼번에
    jobs = []
    if sha:
        jobs.append(("runs", lambda: _safe(lambda: gh.check_runs(sha), []) or []))
        jobs.append(("tree", lambda: _safe(lambda: gh.tree(sha), None)))
    for c in (commits or [])[:5]:  # 최근 커밋 5개에 들어간 키 모양 (추가된 줄만 본다)
        jobs.append(("detail", lambda c=c: (c, _safe(lambda: gh.commit(c["sha"]), {}) or {})))
    for m in cand:
        jobs.append(("revs", lambda m=m: (m, _safe(lambda: gh.pr_reviews(m["number"]), []) or [])))
    for p in prs:
        if p.changes_at:
            jobs.append(("pr_commits", lambda p=p: (p, _safe(lambda: gh.pr_commits(p.number), None))))
    results = par([j for _, j in jobs])
    by = {}
    for (kind, _), res in zip(jobs, results):
        by.setdefault(kind, []).append(res)

    # 1) main 빌드
    if sha:
        from .state import ci_status
        ci, fail_h = ci_status((by.get("runs") or [[]])[0], now)
        sig["main"] = {"sha": sha, "ci": ci, "fail_hours": fail_h}
    # 2) 최근 2주 커밋 (main)
    if commits is not None:
        if sig["main"] and commits:
            head = next((c for c in commits if c["sha"] == sha), commits[0])
            sig["main"].update(author=head["login"], message=head["message"], url=head["url"])
        for c, detail in by.get("detail", []):
            for f in detail.get("files", []) or []:
                kinds = key_kinds(f.get("patch") or "")
                if kinds:
                    sig["keys"].append({"path": f.get("filename"), "kinds": kinds, "sha": c["sha"], "login": c["login"], "name": c["name"], "url": c["url"]})

    # 3) 팀원별 마지막 활동 (푸시·PR·리뷰·댓글·이슈, 어느 브랜치든)
    if ev is not None:
        last: dict[str, str] = {}
        for e in ev:
            who, at = (e.get("actor") or {}).get("login"), e.get("created_at")
            if who and at and at > last.get(who, ""):
                last[who] = at
        for c in commits or []:
            if c["login"] and c["at"] and c["at"] > last.get(c["login"], ""):
                last[c["login"]] = c["at"]
        sig["activity"] = last
        sig["pushes"] = [{"login": (e.get("actor") or {}).get("login"), "at": e.get("created_at"), "ref": (e.get("payload") or {}).get("ref", "")}
                         for e in ev if e.get("type") == "PushEvent"]

    # 4) 수락하지 않은 초대 (저장소 관리 권한이 없으면 못 읽는다)
    if inv is not None:
        sig["invites"] = [{"login": (i.get("invitee") or {}).get("login") or "(이메일 초대)", "at": i.get("created_at"), "expired": bool(i.get("expired"))} for i in inv]

    # 5) 파일 목록: 큰 파일 · 올리면 안 되는 폴더 · 비밀키 파일
    blobs = (by.get("tree") or [None])[0]
    if blobs is not None:
        sig["tree"] = {"big": [{"path": b["path"], "size": b.get("size", 0)} for b in blobs if (b.get("size") or 0) >= BIG_BYTES][:5],
                       "junk": sorted({JUNK_DIR.search(b["path"]).group(0).strip("/").split("/")[-1] for b in blobs if JUNK_DIR.search(b["path"])}),
                       "secrets": [b["path"] for b in blobs if secret_file(b["path"])][:5]}

    # 6) 최근 3일 안에 리뷰 승인 없이 머지된 PR
    if merged is not None:
        unreviewed = []
        for m, revs in by.get("revs", []):
            author = (m.get("user") or {}).get("login")
            if any(r.get("state") == "APPROVED" and (r.get("user") or {}).get("login") != author for r in revs):
                continue
            unreviewed.append(m)
        # 이미 리뷰를 부탁했으면(Bento가 남긴 댓글) 다시 제안하지 않는다 — 같은 댓글이 두 번 달리지 않게. 댓글은 후보에만 읽는다
        cms = par([lambda m=m: _safe(lambda: gh.issue_comments(m["number"]), []) or [] for m in unreviewed])
        out = []
        for m, c in zip(unreviewed, cms):
            if not any("bento-op:" in (x.get("body") or "") for x in c):
                out.append({"number": m["number"], "title": m.get("title", ""), "author": (m.get("user") or {}).get("login"),
                            "url": m.get("html_url") or gh.html(f"/pull/{m['number']}"), "merged_at": m.get("merged_at")})
        sig["merged_unreviewed"] = out

    # 7) 수정 요청을 받은 PR: 그 뒤에 새 커밋이 있었는지
    for p, cs in by.get("pr_commits", []):
        if cs is None:
            continue
        sig["changes"][p.number] = max((((c.get("commit") or {}).get("committer") or {}).get("date") or "" for c in cs), default="")

    _CACHE[key] = (time.time(), sig)
    return sig


def clear() -> None:
    _CACHE.clear()
