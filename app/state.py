"""GitHub 응답을 Bento가 판단에 쓰는 저장소 상태로 바꾼다. 판단 함수는 모두 순수 함수라 테스트하기 쉽다."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


def parse_ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def ms_due(s: str | None) -> datetime | None:
    """GitHub 마일스톤 마감은 날짜만 남는다(시각은 버려짐). 그 날짜의 한국 시간 23:59로 본다."""
    if not s:
        return None
    from datetime import timedelta, timezone
    d = s[:10]
    return datetime.fromisoformat(f"{d}T23:59:00+09:00").astimezone(timezone.utc) if len(d) == 10 else parse_ts(s)


def hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600


@dataclass
class PR:
    number: int
    title: str
    author: str
    branch: str
    url: str
    reviewers: list[str]
    approvals: list[str]
    review_wait_hours: float | None
    ci: str  # pass | fail | pending | none
    ci_fail_hours: float | None
    mergeable: str | None  # clean | dirty | blocked | unstable | unknown
    files: list[str]
    body: str = ""
    draft: bool = False


@dataclass
class Issue:
    number: int
    title: str
    author: str
    assignees: list[str]
    milestone: str | None
    body: str
    url: str
    state: str = "open"
    created_at: str | None = None


@dataclass
class Milestone:
    number: int
    title: str
    due_on: str | None
    open: int
    closed: int
    description: str = ""


@dataclass
class RepoState:
    repo: str
    now: datetime
    members: list[str]
    prs: list[PR]
    issues: list[Issue]
    milestones: list[Milestone]
    merged_refs: dict[int, int] = field(default_factory=dict)  # 이슈 번호 → 그 이슈를 언급한 머지된 PR
    file_authors: dict[str, list[str]] = field(default_factory=dict)
    roles: dict[str, list[str]] = field(default_factory=dict)  # 아이디 → 역할 코드 (팀이 승인해서 정한 것)
    leads: list[str] = field(default_factory=list)  # 팀장 (저장소 관리자)

    def pr(self, n: int) -> PR | None:
        return next((p for p in self.prs if p.number == n), None)

    def issue(self, n: int) -> Issue | None:
        return next((i for i in self.issues if i.number == n), None)

    def milestone(self, title: str) -> Milestone | None:
        return next((m for m in self.milestones if m.title == title), None)


def review_wait_hours(timeline: list[dict], reviews: list[dict], now: datetime) -> float | None:
    """마지막 리뷰 요청 이후 리뷰가 없으면 기다린 시간을, 아니면 None."""
    asked = [parse_ts(e.get("created_at")) for e in timeline if e.get("event") == "review_requested" and e.get("created_at")]
    if not asked:
        return None
    last = max(asked)
    if any((t := parse_ts(r.get("submitted_at"))) and t > last for r in reviews):
        return None
    return round(hours(last, now), 1)


def approvals(reviews: list[dict]) -> list[str]:
    latest: dict[str, str] = {}
    for r in sorted(reviews, key=lambda r: r.get("submitted_at") or ""):
        if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[r["user"]["login"]] = r["state"]
    return sorted(u for u, s in latest.items() if s == "APPROVED")


def ci_status(runs: list[dict], now: datetime) -> tuple[str, float | None]:
    if not runs:
        return "none", None
    failed = [r for r in runs if r.get("conclusion") in ("failure", "timed_out", "cancelled", "action_required")]
    if failed:
        since = min((parse_ts(r.get("completed_at")) for r in failed if r.get("completed_at")), default=None)
        return "fail", round(hours(since, now), 1) if since else None
    if any(r.get("status") != "completed" for r in runs):
        return "pending", None
    return "pass", None


def issue_refs(text: str) -> set[int]:
    return {int(n) for n in re.findall(r"(?<![\w/])#(\d{1,6})\b", text or "")}


def build_state(gh, now: datetime | None = None) -> RepoState:
    now = now or datetime.now(timezone.utc)
    members = gh.members()
    prs: list[PR] = []
    authors: dict[str, list[str]] = {}
    for p in gh.open_prs()[:15]:
        n = p["number"]
        reviews = gh.pr_reviews(n)
        ci, fail_h = ci_status(gh.check_runs(p["head"]["sha"]), now)
        files = gh.pr_files(n)
        prs.append(PR(
            number=n, title=p["title"], author=p["user"]["login"], branch=p["head"]["ref"], url=p["html_url"],
            reviewers=[u["login"] for u in p.get("requested_reviewers", [])],
            approvals=approvals(reviews),
            review_wait_hours=review_wait_hours(gh.timeline(n), reviews, now),
            ci=ci, ci_fail_hours=fail_h,
            mergeable=gh.pr_detail(n).get("mergeable_state"),
            files=files, body=(p.get("body") or "")[:2000], draft=bool(p.get("draft")),
        ))
        for f in files[:3]:
            if f not in authors:
                authors[f] = gh.file_authors(f)
    merged_refs: dict[int, int] = {}
    for mp in gh.merged_prs():
        for ref in issue_refs(f"{mp['title']}\n{mp.get('body') or ''}"):
            merged_refs.setdefault(ref, mp["number"])
    issues = [
        Issue(number=i["number"], title=i["title"], author=i["user"]["login"],
              assignees=[a["login"] for a in i.get("assignees", [])],
              milestone=(i.get("milestone") or {}).get("title"), body=(i.get("body") or "")[:2000], url=i["html_url"],
              created_at=i.get("created_at"))
        for i in gh.open_issues()
    ]
    milestones = [Milestone(m["number"], m["title"], m.get("due_on"), m.get("open_issues", 0), m.get("closed_issues", 0), m.get("description") or "") for m in gh.milestones()]
    from .ownership import read_roles
    roles = {m: r for m, r in read_roles(gh).items() if m in members}
    try:
        leads = [m for m in gh.leads() if m in members] if hasattr(gh, "leads") else []
    except Exception:  # noqa: BLE001 - 못 읽으면 팀장 없이
        leads = []
    return RepoState(gh.repo, now, members, prs, issues, milestones, merged_refs, authors, roles, leads)
