"""승인이 미뤄지지 않게: 주인 정하기 → 단계적 알림 → (팀이 켠 일만) 반대가 없으면 실행.

- 주인: 승인 대기마다 한 명. "누군가 하겠지"를 "내 차례"로 바꾼다.
- 단계: 0 = 주인에게만, 1 = 12시간 · 주인에게 다시 + 팀에 보임, 2 = 24시간 · 누구나 승인, 팀 전체 알림.
- 반대가 없으면 실행: 팀 규칙으로 켠 일(리뷰어 지정·리마인드 댓글)만. Bento가 GitHub에 예고 댓글을 달고,
  정한 시간 동안 팀원 누구도 👎를 누르지 않으면 실행한다. 예고·반대·실행이 모두 GitHub에 남는다.
"""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from .actions import Comment, RequestReviewers
from .github import GitHubError
from .state import RepoState, hours, parse_ts

KST = timezone(timedelta(hours=9))
REMIND_H, OPEN_H = 12, 24

# ---------- 오늘의 당번 ----------


def duty(members: list[str], now: datetime | None = None) -> str | None:
    """팀원이 하루씩 돌아가며 맡는다 (한국 날짜 기준)."""
    if not members:
        return None
    day = (now or datetime.now(timezone.utc)).astimezone(KST).date().toordinal()
    return sorted(members)[day % len(members)]


def _num(item_id: str) -> int | None:
    m = re.match(r"[a-z]+-(\d+)", item_id)
    return int(m.group(1)) if m else None


def owner_and_age(item: dict, state: RepoState, review_wait_hours: float, now: datetime) -> tuple[str | None, float]:
    kind, n = item["id"].split("-")[0], _num(item["id"])
    pr = state.pr(n) if n else None
    if kind == "review" and pr:  # 리뷰가 밀려 제일 답답한 사람 = PR 작성자
        return pr.author, max(0.0, (pr.review_wait_hours or 0) - review_wait_hours)
    if kind == "fix" and pr:
        return pr.author, pr.ci_fail_hours or 0.0
    if kind in ("merge", "conflict") and pr:
        return pr.author, 0.0
    if kind == "assign":
        ages = [hours(t, now) for i in state.issues if not i.assignees and (t := parse_ts(i.created_at))]
        return duty(state.members, now), max(ages, default=0.0)
    return duty(state.members, now), 0.0


def level(age: float) -> int:
    return 2 if age >= OPEN_H else 1 if age >= REMIND_H else 0


# ---------- 팀 규칙 (저장소 라벨 설명에 보관) ----------
RULES = "bento-rules"
CATEGORIES = {"reviewers": "리뷰어 지정", "reminders": "리마인드 댓글"}


def category(action) -> str | None:
    if isinstance(action, RequestReviewers):
        return "reviewers"
    if isinstance(action, Comment):
        return "reminders"
    return None


def read_rules(gh) -> dict:
    try:
        desc = gh.get_label(RULES).get("description") or ""
    except GitHubError:
        return {"lazy": [], "hours": 24}
    kv = dict(p.split("=", 1) for p in desc.split(";") if "=" in p)
    lazy = [c for c in kv.get("lazy", "").split(",") if c in CATEGORIES]
    try:
        h = min(72, max(6, int(kv.get("hours", "24"))))
    except ValueError:
        h = 24
    return {"lazy": lazy, "hours": h}


def write_rules(gh, lazy: list[str], hours_: int) -> None:
    desc = f"lazy={','.join(lazy)};hours={hours_}"
    try:
        gh.update_label(RULES, desc)
    except GitHubError:
        gh.create_label(RULES, "EDEDED", desc)


def lazy_ok(actions: list, rules: dict) -> bool:
    cats = [category(a) for a in actions]
    return bool(actions) and all(c and c in rules["lazy"] for c in cats)


# ---------- 반대가 없으면 실행 ----------
MARK = re.compile(r"<!-- bento-lazy:([0-9a-f]{12}) (\d+) -->")


def key_of(repo: str, actions: list) -> str:
    return hashlib.sha256(json.dumps([repo, [a.model_dump() for a in actions]], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def find_notice(gh, number: int, key: str):
    for c in gh.issue_comments(number):
        m = MARK.search(c.get("body") or "")
        if m and m.group(1) == key[:12]:
            return c, int(m.group(2))
    return None, None


def vetoed_by(gh, comment: dict, members: list[str]) -> list[str]:
    try:
        rs = gh.comment_reactions(comment["id"])
    except GitHubError:
        return []
    return sorted({r["user"]["login"] for r in rs if r.get("content") == "-1" and r["user"]["login"] in members})


def notice_text(owner: str | None, label: str, hours_: int, key: str, now: datetime) -> str:
    who = f"@{owner} 님이 아직 승인하지 않아서, " if owner else ""
    return (f"{who}팀 규칙에 따라 **{hours_}시간 안에 반대가 없으면** Bento가 '{label}'을(를) 실행해요.\n"
            f"반대하려면 이 댓글에 👎를 눌러 주세요. 한 명이라도 반대하면 실행하지 않아요.\n"
            f"<!-- bento-lazy:{key[:12]} {int(now.timestamp())} -->")


def lazy_status(gh, item: dict, actions: list, rules: dict, members: list[str], now: datetime) -> dict:
    """이 승인 대기의 '반대가 없으면 실행' 상태. 저장소를 읽기만 한다."""
    n = _num(item["id"])
    if not n or not lazy_ok(actions, rules):
        return {"eligible": False}
    key = key_of(gh.repo, actions)
    c, ts = find_notice(gh, n, key)
    if not c:
        return {"eligible": True, "status": "none", "number": n}
    due = datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=rules["hours"])
    v = vetoed_by(gh, c, members)
    return {"eligible": True, "status": "vetoed" if v else "waiting", "vetoed_by": v, "number": n,
            "executes_at": due.isoformat(), "url": c.get("html_url"), "due": due <= now}
