"""계획 대비 진행을 관찰하고, 늦어지면 다시 계획한다.

관찰 → 판단 → 다시 계획 → (사람 승인) → 실행. 판단은 규칙으로 해서 토큰이 들지 않는다.
"""

from datetime import datetime, timedelta, timezone

from .state import RepoState, parse_ts

PLAN_PREFIX = "계획 · "
SUBMIT_MS = "해커톤 제출"
MIN_STEP = timedelta(hours=6)  # 단계 하나에 최소 이 정도는 있어야 현실적이다


def _h(td: timedelta) -> int:
    return int(td.total_seconds() // 3600)


def least_loaded(state: RepoState, exclude: set[str] = frozenset()) -> str | None:
    """열린 이슈를 가장 적게 맡은 팀원."""
    load = {m: 0 for m in state.members if m not in exclude}
    for i in state.issues:
        for a in i.assignees:
            if a in load:
                load[a] += 1
    return min(load, key=lambda m: (load[m], m)) if load else None


def replan(steps: list[dict], now: datetime, deadline: datetime, done: set[str]) -> list[dict]:
    """끝나지 않은 단계를 지금부터 마감 사이에 다시 놓는다. 순서는 지키고, 늦지 않은 단계는 되도록 그대로 둔다.

    steps: [{"title", "due"(ISO)}], 돌려주는 값: 날짜가 바뀐 단계 [{"title", "old", "new"}]
    """
    limit = deadline - timedelta(hours=3)
    todo = sorted((s for s in steps if s["title"] not in done and s["title"] not in ("제출 마감", "최종 점검", "신청서 제출")), key=lambda s: s["due"])
    n = len(todo)
    if not n or limit <= now:
        return []
    ideal = [now + (limit - now) * (k + 1) / (n + 1) for k in range(n)]  # 남은 시간을 고르게 나눈 자리
    olds = [datetime.fromisoformat(s["due"]) for s in todo]
    # 1) 아직 안 지났고 마감 안쪽인 단계는 그대로, 지난 단계는 고르게 나눈 자리로
    new = [o if now + timedelta(hours=1) <= o <= limit else ideal[k] for k, o in enumerate(olds)]
    # 2) 순서가 꼬이거나 1시간 안으로 붙으면 전부 고르게 다시 놓는다
    if any(b <= a + timedelta(hours=1) for a, b in zip(new, new[1:])):
        new = ideal
    return [{"title": s["title"], "old": o.isoformat(), "new": d.replace(microsecond=0).isoformat()}
            for s, o, d in zip(todo, olds, new) if abs((d - o).total_seconds()) > 3600]


def check(state: RepoState, plan: list[dict] | None, deadline_iso: str | None, now: datetime | None = None,
          plan_start_iso: str | None = None) -> dict:
    """진행 점검. findings(발견), moves(바꿀 날짜), assign(담당 제안), scope(범위 줄이기 제안)."""
    now = now or datetime.now(timezone.utc)
    findings: list[dict] = []
    ms_plan = {m.title[len(PLAN_PREFIX):]: m for m in state.milestones if m.title.startswith(PLAN_PREFIX)}
    steps = [{"title": t, "due": parse_ts(m.due_on).isoformat()} for t, m in ms_plan.items() if m.due_on]
    done: set[str] = set()
    for s in plan or []:  # 저장소에 없는 단계는 이 기기 계획에서
        if s.get("kind", "step") == "step" and s["title"] not in ms_plan:
            steps.append({"title": s["title"], "due": s["due"], "local": True})
            if s.get("done"):
                done.add(s["title"])
    late: list[str] = []
    for t, m in ms_plan.items():
        total = m.open + m.closed
        if total and m.open == 0:
            done.add(t)
            continue
        due = parse_ts(m.due_on)
        if not due:
            continue
        unassigned = [i for i in state.issues if i.milestone == m.title and not i.assignees]
        ratio = m.closed / total if total else 0
        if due < now and m.open:
            late.append(t)
            findings.append({"id": f"late:{t}", "sev": "긴급", "title": f"'{t}' 단계가 {_h(now - due)}시간 늦었어요",
                             "why": f"열린 이슈 {m.open}개 · 닫힌 이슈 {m.closed}개" + (f" · 담당자 없음 {len(unassigned)}개" if unassigned else ""),
                             "milestone": m.title})
        elif due - now < timedelta(hours=36) and m.open and ratio < 0.5:
            late.append(t)
            findings.append({"id": f"risk:{t}", "sev": "주의", "title": f"'{t}' 마감이 {_h(due - now)}시간 남았는데 {int(ratio * 100)}%예요",
                             "why": f"열린 이슈 {m.open}개" + (f" · 담당자 없음 {len(unassigned)}개" if unassigned else ""), "milestone": m.title})
    for s in steps:
        if s.get("local") and s["title"] not in done and datetime.fromisoformat(s["due"]) < now:
            findings.append({"id": f"local:{s['title']}", "sev": "확인", "title": f"계획상 '{s['title']}' 날짜가 지났어요",
                             "why": "저장소 마일스톤이 없어서 끝났는지 알 수 없어요. 끝났으면 '끝났어요'를 눌러 주세요", "local": True})

    deadline = datetime.fromisoformat(deadline_iso) if deadline_iso else None
    sub = state.milestone(SUBMIT_MS)
    if sub and deadline and plan_start_iso:
        start = datetime.fromisoformat(plan_start_iso)
        if deadline > start:
            t_ratio = min(1, max(0, (now - start) / (deadline - start)))
            total = sub.open + sub.closed
            w_ratio = sub.closed / total if total else 1
            if total and t_ratio - w_ratio > 0.25:
                findings.append({"id": "pace", "sev": "주의", "title": f"전체 진도가 늦어요: 시간 {int(t_ratio * 100)}% 지났는데 제출물 {int(w_ratio * 100)}% 끝남",
                                 "why": f"'{SUBMIT_MS}' 열린 이슈 {sub.open}개"})

    # 담당 제안: 늦거나 위험한 단계의 담당자 없는 이슈 → 일이 가장 적은 팀원
    assign, given = [], {}
    for f in findings:
        if not f.get("milestone"):
            continue
        for i in state.issues:
            if i.milestone == f["milestone"] and not i.assignees:
                who = least_loaded(state)
                if who:
                    given[who] = given.get(who, 0) + 1
                    state_issue_load(state, i, who)
                    assign.append({"issue": i.number, "title": i.title, "user": who})

    moves = replan(steps, now, deadline, done) if deadline and (late or any(f["id"] == "pace" or f.get("local") for f in findings)) else []
    for mv in moves:
        mv["repo"] = mv["title"] in ms_plan
    scope = None
    if deadline:
        remain = deadline - timedelta(hours=3) - now
        todo = [s for s in steps if s["title"] not in done]
        if todo and remain < MIN_STEP * len(todo):
            scope = (f"남은 시간({max(0, _h(remain))}시간)이 남은 단계 {len(todo)}개를 하기에 빠듯해요. "
                     "시연에 꼭 필요한 흐름 하나만 남기고, 나머지 기능 이슈는 제출 마일스톤에서 빼는 걸 추천해요.")
    return {"findings": findings, "moves": moves, "assign": assign, "scope": scope,
            "summary": summary(findings, moves, assign)}


def state_issue_load(state: RepoState, issue, who: str) -> None:
    """같은 점검 안에서 한 사람에게 몰리지 않도록 제안한 담당을 바로 반영한다."""
    issue.assignees = [who]


def summary(findings, moves, assign) -> str:
    if not findings:
        return ""
    lines = ["Bento가 진행을 점검했어요."] + [f"· {f['title']}" for f in findings[:5]]
    if moves:
        lines.append(f"→ 남은 단계 {len(moves)}개의 날짜를 다시 잡는 걸 제안해요")
    if assign:
        lines.append(f"→ 담당자 없는 이슈 {len(assign)}개를 일이 적은 팀원에게 나누는 걸 제안해요")
    return "\n".join(lines)
