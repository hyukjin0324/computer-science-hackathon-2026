"""승인이 미뤄지지 않게: 주인 정하기 → 단계적 알림 → (팀이 켠 일만) 반대가 없으면 실행.

- 주인: 승인 대기마다 한 명. "누군가 하겠지"를 "내 차례"로 바꾼다.
- 단계: 0 = 주인에게만, 1 = 12시간 · 주인에게 다시 + 팀에 보임, 2 = 24시간 · 누구나 승인, 팀 전체 알림.
- 반대가 없으면 실행: 팀 규칙으로 켠 일(리뷰어 지정·리마인드 댓글)만. Bento가 GitHub에 예고 댓글을 달고,
  정한 시간 동안 팀원 누구도 👎를 누르지 않으면 실행한다. 예고·반대·실행이 모두 GitHub에 남는다.
"""

import hashlib
import json
import random
import re
from datetime import datetime, timedelta, timezone

from .actions import Comment, RequestReviewers
from .github import GitHubError
from .state import RepoState, hours, parse_ts

KST = timezone(timedelta(hours=9))
REMIND_H, OPEN_H = 12, 24

# ---------- 오늘의 당번 ----------


def _start_ord(rules: dict | None) -> int:
    v = (rules or {}).get("start") or ""
    try:
        return datetime.strptime(v, "%Y%m%d").date().toordinal()
    except ValueError:
        return 0


def duty_order(members: list[str], rules: dict | None = None) -> list[str]:
    """당번 순서. 팀이 순서를 섞어 두었으면(seed) 그 순서로 고정, 아니면 아이디 순."""
    order = sorted(members)
    seed = (rules or {}).get("seed")
    if seed:
        random.Random(seed).shuffle(order)
    return order


def duty_index(now: datetime, rules: dict | None = None) -> int:
    rot = max(1, int((rules or {}).get("rotate") or 1))
    day = now.astimezone(KST).date().toordinal()
    return max(0, day - _start_ord(rules)) // rot


def duty(members: list[str], now: datetime | None = None, rules: dict | None = None) -> str | None:
    """팀원이 정해진 날수(기본 하루)씩 돌아가며 맡는다 (한국 날짜 기준)."""
    if not members:
        return None
    order = duty_order(members, rules)
    return order[duty_index(now or datetime.now(timezone.utc), rules) % len(order)]


def duty_schedule(members: list[str], rules: dict | None, now: datetime, n: int = 5) -> list[dict]:
    """앞으로 돌아올 당번 일정: [{who, from, to}] (날짜는 한국 기준 YYYY-MM-DD). 첫 줄은 지금 당번."""
    if not members:
        return []
    rot = max(1, int((rules or {}).get("rotate") or 1))
    order = duty_order(members, rules)
    idx = duty_index(now, rules)
    first = _start_ord(rules) + idx * rot
    if not _start_ord(rules):
        first = now.astimezone(KST).date().toordinal() - (now.astimezone(KST).date().toordinal() % rot)
    out = []
    for k in range(n):
        a = first + k * rot
        out.append({"who": order[(idx + k) % len(order)], "from": datetime.fromordinal(a).date().isoformat(), "to": datetime.fromordinal(a + rot - 1).date().isoformat()})
    return out


def _num(item_id: str) -> int | None:
    m = re.match(r"[a-z]+-(\d+)", item_id)
    return int(m.group(1)) if m else None


def owner_and_age(item: dict, state: RepoState, review_wait_hours: float, now: datetime) -> tuple[str | None, float]:
    if "age" in item:  # watch.py가 정한 주인(없으면 오늘 당번)과 기다린 시간
        return (item.get("who") if item.get("who") in state.members else duty(state.members, now, getattr(state, "rules", None))), max(0.0, float(item.get("age") or 0))
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
        return duty(state.members, now, getattr(state, "rules", None)), max(ages, default=0.0)
    return duty(state.members, now, getattr(state, "rules", None)), 0.0


def owner_reason(item: dict, state: RepoState, owner: str | None, now: datetime) -> str:
    """이 일의 주인이 왜 이 사람인지 한 줄. owner_and_age와 같은 기준을 말로 옮긴 것."""
    if not owner:
        return ""
    rules = getattr(state, "rules", None) or {}
    rot = max(1, int(rules.get("rotate") or 1))
    on_duty = f"당번이라서 · {'오늘' if rot == 1 else f'{rot}일마다 돌아가요'}"
    if "age" in item:
        return "이 일과 가장 관련 있는 사람으로 정했어요" if item.get("who") in state.members else on_duty
    kind, n = item["id"].split("-")[0], _num(item["id"])
    if kind in ("review", "fix", "merge", "conflict") and n and state.pr(n):
        return "이 PR을 올린 사람이라서"
    return on_duty


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
    base = {"lazy": [], "hours": 24, "rotate": 1, "seed": "", "start": ""}
    try:
        desc = gh.get_label(RULES).get("description") or ""
    except GitHubError:
        return base
    kv = dict(p.split("=", 1) for p in desc.split(";") if "=" in p)
    lazy = [c for c in kv.get("lazy", "").split(",") if c in CATEGORIES]
    try:
        h = min(72, max(6, int(kv.get("hours", "24"))))
    except ValueError:
        h = 24
    try:
        rot = min(14, max(1, int(kv.get("rot", "1"))))
    except ValueError:
        rot = 1
    seed = kv.get("seed", "") if re.fullmatch(r"[0-9a-f]{1,12}", kv.get("seed", "")) else ""
    start = kv.get("from", "") if re.fullmatch(r"\d{8}", kv.get("from", "")) else ""
    return {"lazy": lazy, "hours": h, "rotate": rot, "seed": seed, "start": start}


def write_rules(gh, lazy: list[str], hours_: int, rotate: int | None = None, seed: str | None = None, start: str | None = None) -> None:
    """팀 규칙 저장. 당번 주기·순서·시작일은 넘기지 않으면 지금 값을 그대로 둔다."""
    cur = read_rules(gh)
    rot = cur["rotate"] if rotate is None else rotate
    sd = cur["seed"] if seed is None else seed
    st = cur["start"] if start is None else start
    desc = f"lazy={','.join(lazy)};hours={hours_}"
    if rot != 1 or sd or st:
        desc += f";rot={rot};seed={sd};from={st}"
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


# ---------- 팀 역할 (팀원마다 라벨 하나: bento-role:아이디 → "be,fe") ----------
ROLES = {"pm": "기획·PM", "fe": "프론트엔드", "be": "백엔드", "ds": "디자인", "pt": "발표"}
ROLE_PREFIX = "bento-role:"
# 할 일 제목 → 맡을 역할 (위에서부터 먼저 맞는 것)
_ROLE_RULES = [
    (r"발표|PPT|슬라이드|피치|시연|영상|시나리오", ["pt", "ds"]),
    (r"디자인|UI|UX|화면\s*설계|피그마|figma", ["ds", "fe"]),
    (r"README|배포|서버|API|DB|데이터베이스|백엔드|인프라", ["be"]),
    (r"프론트|화면|페이지|컴포넌트", ["fe"]),
    (r"MVP|핵심\s*기능|기능|구현|버그", ["fe", "be"]),
    (r"아이디어|역할|기획|최종\s*점검|제출|신청|마무리", ["pm"]),
]


def read_roles(gh) -> dict[str, list[str]]:
    try:
        labels = gh.labels()
    except Exception:  # noqa: BLE001 - 못 읽으면 역할 없이
        return {}
    out = {}
    for lb in labels:
        name = lb.get("name") or ""
        if name.startswith(ROLE_PREFIX):
            codes = [c for c in (lb.get("description") or "").split(",") if c in ROLES]
            if codes:
                out[name[len(ROLE_PREFIX):]] = codes
    return out


def write_roles(gh, roles: dict[str, list[str]]) -> None:
    have = read_roles(gh)
    for login, codes in roles.items():
        name, desc = ROLE_PREFIX + login, ",".join(codes)
        if not codes:
            if login in have:
                gh.delete_label(name)
            continue
        try:
            gh.update_label(name, desc)
        except GitHubError:
            gh.create_label(name, "C5DEF5", desc)


def role_codes_for(title: str) -> list[str]:
    import re
    for pat, codes in _ROLE_RULES:
        if re.search(pat, title, re.I):
            return codes
    return []


def role_names(codes: list[str]) -> str:
    return "·".join(ROLES[c] for c in codes if c in ROLES)


def pick_by_role(members: list[str], roles: dict, load: dict, title: str, skip=()) -> tuple[str | None, str]:
    """할 일 제목에 맞는 역할의 팀원 중 맡은 일이 가장 적은 사람. 맞는 역할이 없으면 맡은 일이 가장 적은 사람."""
    codes = role_codes_for(title)
    pool = [m for m in members if m not in skip]
    fit = [m for m in pool if set(roles.get(m, [])) & set(codes)]
    if fit:
        who = min(fit, key=lambda m: (load.get(m, 0), m))
        return who, f"{role_names([c for c in codes if c in roles.get(who, [])])} 담당"
    if pool:
        who = min(pool, key=lambda m: (load.get(m, 0), m))
        return who, "맡은 일이 가장 적어요"
    return None, ""


_NAMED = re.compile(r"담당\s*[:：]\s*([^)\]\n]+)")


def named_owner(title: str, members: list[str], names: dict[str, str] | None = None, leads: list[str] | None = None) -> str | None:
    """이슈 제목에 '(담당: 혁진)'처럼 담당이 적혀 있으면 그 사람.

    - 아이디나 GitHub 프로필 이름에 맞는 팀원이 있으면 그 아이디
    - 적혀 있지만 누군지 모르면(A·B·발표·전원·한글 이름) '?' → Bento가 마음대로 나누지 않는다
    - 안 적혀 있으면 None
    """
    m = _NAMED.search(title or "")
    if not m:
        return None
    names = names or {}
    for tok in re.split(r"[,+/·\s]+", m.group(1)):
        t = tok.strip().lower()
        if len(t) < 2:
            continue
        for login in members:
            if t == login.lower() or (len(t) >= 3 and login.lower().startswith(t)) or t in (names.get(login) or "").lower():
                return login
    if leads and (title or "").startswith("[팀장]") and leads[0] in members:
        return leads[0]  # '[팀장] … (담당: 혁진)'처럼 팀장 몫으로 적힌 일은 팀장에게
    return "?"
