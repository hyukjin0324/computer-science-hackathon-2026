"""Bento가 할 수 있는 행동 목록, 검증, 실행.

행동은 이 목록이 전부다. 머지·푸시·코드 수정은 없다.
AI가 무엇을 제안하든 여기서 코드가 다시 검사한 뒤에만 실행된다.
"""

import re
from typing import Annotated, Literal, Union

import httpx
from pydantic import BaseModel, Field, TypeAdapter

from .state import RepoState

FOOTER = "\n\n<sub>— 팀원 승인을 받아 Bento가 남겼어요</sub>"
# 영어 낱말은 앞뒤가 영문자가 아닐 때만 ('main'의 ai, 'robot'의 bot 같은 오탐을 막는다)
SUSPICIOUS = re.compile(
    r"((?<![a-z])(?:bento|agent|bot|ai)(?![a-z])|벤토|에이전트|봇)[^\n]{0,30}"
    # 한국어 낱말(권한·토큰·머지 등)은 뒤에 '~해/줘/하세요' 같은 부탁이 붙을 때만. 'AI 사용량 · 권한' 같은 설명은 빼려고
    r"(merge|close|delete|push|token|닫아|지워|(?:머지|삭제|푸시|권한|토큰)[^\n]{0,8}?(?:해|하고|하세요|하라|줘|주세요|달라|적어|넘겨|알려))", re.I)


class RequestReviewers(BaseModel):
    type: Literal["request_reviewers"] = "request_reviewers"
    pr: int
    users: list[str]
    expect_reviewers: list[str] | None = None  # 제안할 때 본 리뷰어. 실행 직전과 다르면 다른 팀원이 먼저 바꾼 것


class Comment(BaseModel):
    type: Literal["comment"] = "comment"
    number: int
    body: str


class CreateIssue(BaseModel):
    type: Literal["create_issue"] = "create_issue"
    title: str
    body: str = ""
    assignees: list[str] = []
    milestone_title: str | None = None


class AssignIssue(BaseModel):
    type: Literal["assign_issue"] = "assign_issue"
    issue: int
    users: list[str]
    expect_assignees: list[str] | None = None  # 제안할 때 본 담당자


class ReassignIssue(BaseModel):
    """할 일을 다른 사람에게 넘기기 (지금 담당자를 바꾼다). 팀 전체에 영향을 주는 변경."""
    type: Literal["reassign_issue"] = "reassign_issue"
    issue: int
    users: list[str]
    expect_assignees: list[str] = []


class CloseIssue(BaseModel):
    type: Literal["close_issue"] = "close_issue"
    issue: int
    comment: str


class CreateMilestone(BaseModel):
    type: Literal["create_milestone"] = "create_milestone"
    title: str
    due_on: str | None = None
    description: str = ""


class UpdateMilestone(BaseModel):
    type: Literal["update_milestone"] = "update_milestone"
    title: str
    due_on: str
    reason: str = ""
    expect_due: str | None = None  # 제안할 때 본 마감


class SetTeamRules(BaseModel):
    type: Literal["set_team_rules"] = "set_team_rules"
    lazy: list[str]  # 반대가 없으면 실행할 일: reviewers, reminders
    hours: int = 24


class SetRoles(BaseModel):
    type: Literal["set_roles"] = "set_roles"
    roles: dict[str, list[str]]  # 아이디 → 역할 코드


class SetMilestone(BaseModel):
    type: Literal["set_milestone"] = "set_milestone"
    issues: list[int]
    milestone_title: str


class TeamMessage(BaseModel):
    type: Literal["team_message"] = "team_message"
    text: str


Action = Annotated[Union[RequestReviewers, Comment, CreateIssue, AssignIssue, CloseIssue, CreateMilestone, UpdateMilestone, SetMilestone, SetTeamRules, SetRoles, ReassignIssue, TeamMessage], Field(discriminator="type")]
ActionList = TypeAdapter(list[Action])

# 승인 화면에서 사람이 고칠 수 있는 글 필드
EDITABLE = {"comment": "body", "close_issue": "comment", "team_message": "text", "create_issue": "body"}


STALE = "다른 팀원이 먼저 바꿨어요 · "


def _same_time(a: str | None, b: str | None) -> bool:
    from datetime import datetime
    if not a or not b:
        return a == b
    try:
        return datetime.fromisoformat(a.replace("Z", "+00:00")) == datetime.fromisoformat(b.replace("Z", "+00:00"))
    except ValueError:
        return a == b


def is_suspicious(text: str) -> bool:
    return bool(SUSPICIOUS.search(text or ""))


def _text_problem(text: str, limit: int = 1500) -> str | None:
    if not text or not text.strip():
        return "내용이 비어 있어요"
    if len(text) > limit:
        return f"내용이 너무 길어요 ({limit}자 이하)"
    if re.search(r"@(everyone|here)\b", text):
        return "전체 호출(@everyone, @here)은 보낼 수 없어요"
    return None


def validate(actions: list, state: RepoState) -> list[str]:
    """실행 전에 저장소의 지금 상태와 맞는지 검사한다. 문제를 한국어 문장 목록으로 돌려준다."""
    errors: list[str] = []
    members = set(state.members)
    new_milestones: set[str] = set()
    new_titles: set[str] = set()
    for i, a in enumerate(actions, 1):
        bad = lambda m: errors.append(f"{i}번 행동: {m}")  # noqa: E731
        users = getattr(a, "users", None) or getattr(a, "assignees", None) or []
        if stranger := [u for u in users if u not in members]:
            bad(f"팀원 목록에 없는 사람이에요 ({', '.join(stranger)})")
        if isinstance(a, RequestReviewers):
            pr = state.pr(a.pr)
            if pr and a.expect_reviewers is not None and sorted(pr.reviewers) != sorted(a.expect_reviewers):
                bad(STALE + f"PR #{a.pr} 리뷰어가 지금은 {', '.join(pr.reviewers) or '없음'}이에요")
            if not pr:
                bad(f"열린 PR #{a.pr}이 없어요")
            elif pr.author in a.users:
                bad("PR 작성자는 자기 PR의 리뷰어가 될 수 없어요")
            if not a.users:
                bad("리뷰어가 비어 있어요")
        elif isinstance(a, Comment):
            if not state.pr(a.number) and not state.issue(a.number):
                bad(f"열린 PR이나 이슈 #{a.number}이 없어요")
            if p := _text_problem(a.body):
                bad(p)
        elif isinstance(a, CreateIssue):
            if not a.title.strip() or len(a.title) > 100:
                bad("이슈 제목이 비었거나 너무 길어요")
            if a.title in new_titles or any(x.title == a.title for x in state.issues):
                bad(f"같은 제목의 열린 이슈가 이미 있어요 ('{a.title}')")
            new_titles.add(a.title)
            if a.milestone_title and not (state.milestone(a.milestone_title) or a.milestone_title in new_milestones):
                bad(f"마일스톤 '{a.milestone_title}'이 없어요")
            if a.body and (p := _text_problem(a.body, 5000)):
                bad(p)
        elif isinstance(a, (AssignIssue, CloseIssue, ReassignIssue)):
            iss = state.issue(a.issue)
            if isinstance(a, ReassignIssue) and any(u not in state.members for u in a.users):
                bad("저장소 팀원에게만 넘길 수 있어요")
            if iss and isinstance(a, (AssignIssue, ReassignIssue)) and a.expect_assignees is not None and sorted(iss.assignees) != sorted(a.expect_assignees):
                bad(STALE + f"이슈 #{a.issue} 담당자가 지금은 {', '.join(iss.assignees) or '없음'}이에요")
            if not iss:
                bad((STALE + f"이슈 #{a.issue}이 이미 닫혔어요") if isinstance(a, CloseIssue) else f"열린 이슈 #{a.issue}이 없어요")
            elif is_suspicious(iss.body):
                bad(f"이슈 #{a.issue}에는 에이전트에게 하는 의심스러운 지시가 있어서 건드리지 않아요")
            if isinstance(a, (AssignIssue, ReassignIssue)) and not a.users:
                bad("담당자가 비어 있어요")
            if isinstance(a, CloseIssue) and (p := _text_problem(a.comment)):
                bad(p)
        elif isinstance(a, CreateMilestone):
            if not a.title.strip() or len(a.title) > 60:
                bad("마일스톤 이름이 비었거나 너무 길어요")
            if state.milestone(a.title):
                bad(f"마일스톤 '{a.title}'이 이미 있어요")
            new_milestones.add(a.title)
        elif isinstance(a, UpdateMilestone):
            ms = state.milestone(a.title)
            if not ms:
                bad(f"마일스톤 '{a.title}'이 없어요")
            elif a.expect_due is not None and not _same_time(ms.due_on, a.expect_due):
                bad(STALE + f"마일스톤 '{a.title}' 마감이 그새 바뀌었어요")
        elif isinstance(a, SetMilestone):
            if not (state.milestone(a.milestone_title) or a.milestone_title in new_milestones):
                bad(f"마일스톤 '{a.milestone_title}'이 없어요")
            for n in a.issues:
                iss = state.issue(n)
                if not iss:
                    bad(f"열린 이슈 #{n}이 없어요")
                elif is_suspicious(iss.body):
                    bad(f"이슈 #{n}은 의심 지시가 있어서 건드리지 않아요")
        elif isinstance(a, SetTeamRules):
            if not set(a.lazy) <= {"reviewers", "reminders"}:
                bad("반대가 없으면 실행할 수 있는 건 리뷰어 지정과 리마인드 댓글뿐이에요")
            if not 6 <= a.hours <= 72:
                bad("기다리는 시간은 6~72시간 사이로 정해 주세요")
        elif isinstance(a, SetRoles):
            from .ownership import ROLES
            if not a.roles:
                bad("정할 역할이 없어요")
            for who, codes in a.roles.items():
                if who not in state.members:
                    bad(f"{who}님은 이 저장소 팀원이 아니에요")
                if not set(codes) <= set(ROLES) or len(codes) > 3:
                    bad("역할은 기획·PM, 프론트엔드, 백엔드, 디자인, 발표 중에서 3개까지 골라 주세요")
        elif isinstance(a, TeamMessage):
            if p := _text_problem(a.text, 1800):
                bad(p)
    return errors


def _kst(iso: str | None) -> str:
    if not iso:
        return "날짜 미정"
    from datetime import datetime, timedelta, timezone
    d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=9)))
    return f"{d.month}월 {d.day}일 {d:%H:%M} (한국 시간)"


def label(a, discord: bool) -> tuple[str, str]:
    """승인 화면에 보여 줄 한 줄 설명과 보조 설명."""
    if isinstance(a, RequestReviewers):
        return f"PR #{a.pr} 검토를 {', '.join(a.users)}님에게 부탁하기", "이미 부탁한 사람은 그대로 둬요"
    if isinstance(a, Comment):
        return f"#{a.number}에 댓글 남기기", "Bento가 쓴 댓글이라고 표시해요"
    if isinstance(a, CreateIssue):
        return f"할 일 추가: {a.title}", (f"담당 {', '.join(a.assignees)}" if a.assignees else "담당은 나중에 정해요") + (f" · '{a.milestone_title}'에 넣어요" if a.milestone_title else "")
    if isinstance(a, AssignIssue):
        return f"할 일 #{a.issue}을 {', '.join(a.users)}님에게 맡기기", ""
    if isinstance(a, ReassignIssue):
        return f"할 일 #{a.issue}을 {', '.join(a.expect_assignees) or '담당 없음'} → {', '.join(a.users)}님에게 넘기기", "일이 몰린 사람의 일을 나눠요"
    if isinstance(a, CloseIssue):
        return f"할 일 #{a.issue} 닫기", "이유를 댓글로 남겨요 · 다시 열 수 있어요"
    if isinstance(a, CreateMilestone):
        return f"'{a.title}' 마감일 만들기", _kst(a.due_on) if a.due_on else "공고에서 날짜를 못 찾았어요 · 나중에 정할 수 있어요"
    if isinstance(a, UpdateMilestone):
        return f"'{a.title}' 마감일을 {_kst(a.due_on)}로 옮기기", a.reason or "다시 짠 계획에 맞춰요"
    if isinstance(a, SetMilestone):
        return f"할 일 {', '.join('#' + str(n) for n in a.issues)}을 '{a.milestone_title}'에 묶기", ""
    if isinstance(a, SetTeamRules):
        names = {"reviewers": "리뷰어 지정", "reminders": "리마인드 댓글"}
        return "Bento · 팀 규칙 바꾸기", ("반대가 없으면 실행: " + ", ".join(names[c] for c in a.lazy) + f" · {a.hours}시간 기다림") if a.lazy else "반대가 없으면 실행: 끄기 (모두 승인 필요)"
    if isinstance(a, SetRoles):
        from .ownership import role_names
        return "Bento · 팀 역할 정하기", " · ".join(f"{w} {role_names(c) or '역할 없음'}" for w, c in a.roles.items())
    if isinstance(a, TeamMessage):
        return ("디스코드 팀 채널에 메시지", "") if discord else ("팀 메시지 (메신저 미연결)", "디스코드를 연결하기 전까지는 보내지 않고 복사만 할 수 있어요")
    return "알 수 없는 행동", ""


def _posted(gh, n: int, marker: str) -> bool:
    """같은 댓글을 이미 남겼는지 (다른 서버가 먼저 실행했을 때)."""
    try:
        return any(marker in (c.get("body") or "") for c in gh.issue_comments(n))
    except Exception:  # noqa: BLE001 - 못 읽으면 그냥 남긴다
        return False


def footer(by: str | None) -> str:
    if by == "lazy":
        return "\n\n<sub>— 정한 시간 동안 반대가 없어서, 팀 규칙에 따라 Bento가 남겼어요</sub>"
    if by and "|" in by:  # "제안한 사람|승인한 사람"
        p, a = by.split("|", 1)
        return f"\n\n<sub>— 제안: {p} · 승인: {a} · Bento가 남겼어요</sub>" if p and p != a else f"\n\n<sub>— {a}님 승인으로 Bento가 남겼어요</sub>"
    return f"\n\n<sub>— {by}님 승인으로 Bento가 남겼어요</sub>" if by else FOOTER


def execute(actions: list, gh, settings, op: str | None = None, by: str | None = None) -> list[dict]:
    FOOT = footer(by)
    """검증을 통과한 행동을 순서대로 실행한다. 실패하면 거기서 멈추고 그때까지의 결과를 돌려준다."""
    results: list[dict] = []
    milestone_numbers = {m["title"]: m["number"] for m in gh.milestones()}
    for k, a in enumerate(actions):
        title, _ = label(a, bool(settings.discord_webhook))
        try:
            url = None
            if isinstance(a, RequestReviewers):
                gh.request_reviewers(a.pr, a.users)
                url = gh.html(f"/pull/{a.pr}")
            elif isinstance(a, Comment):
                marker = f"<!-- bento-op:{op}-{k} -->" if op else ""
                if marker and _posted(gh, a.number, marker):
                    results.append({"label": title, "ok": True, "skipped": True, "note": "같은 댓글이 이미 있어서 다시 남기지 않았어요"})
                    continue
                url = gh.comment(a.number, a.body + FOOT + (f"\n{marker}" if marker else "")).get("html_url")
            elif isinstance(a, CreateIssue):
                url = gh.create_issue(a.title, (a.body or "") + FOOT, a.assignees, milestone_numbers.get(a.milestone_title or "")).get("html_url")
            elif isinstance(a, AssignIssue):
                gh.add_assignees(a.issue, a.users)
                url = gh.html(f"/issues/{a.issue}")
            elif isinstance(a, ReassignIssue):
                gh.update_issue(a.issue, assignees=a.users)
                url = gh.html(f"/issues/{a.issue}")
            elif isinstance(a, CloseIssue):
                gh.comment(a.issue, a.comment + FOOT)
                gh.update_issue(a.issue, state="closed", state_reason="completed")
                url = gh.html(f"/issues/{a.issue}")
            elif isinstance(a, CreateMilestone):
                m = gh.create_milestone(a.title, a.due_on, a.description)
                milestone_numbers[a.title] = m["number"]
                url = m.get("html_url")
            elif isinstance(a, UpdateMilestone):
                gh.update_milestone(milestone_numbers[a.title], a.due_on)
                url = gh.html("/milestones")
            elif isinstance(a, SetMilestone):
                for n in a.issues:
                    gh.update_issue(n, milestone=milestone_numbers[a.milestone_title])
            elif isinstance(a, SetTeamRules):
                from .ownership import write_rules
                write_rules(gh, a.lazy, a.hours)
                url = gh.html("/labels")
            elif isinstance(a, SetRoles):
                from .ownership import write_roles
                write_roles(gh, a.roles)
                url = gh.html("/labels")
            elif isinstance(a, TeamMessage):
                if not settings.discord_webhook:
                    results.append({"label": title, "ok": True, "skipped": True, "note": "메신저가 연결되지 않아 보내지 않았어요. 복사해서 팀 채팅에 붙여넣어 주세요."})
                    continue
                r = httpx.post(settings.discord_webhook, json={"content": a.text, "allowed_mentions": {"parse": []}}, timeout=10)
                r.raise_for_status()
            results.append({"label": title, "ok": True, "url": url})
        except Exception as e:  # noqa: BLE001 - 사용자에게 어디서 멈췄는지 보여 준다
            results.append({"label": title, "ok": False, "error": str(e)[:200]})
            break
    return results
