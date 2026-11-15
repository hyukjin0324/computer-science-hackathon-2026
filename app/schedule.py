"""마감에서 거꾸로 계산해 팀플 작업 계획을 짠다. AI를 쓰지 않는다."""

from datetime import datetime, timedelta

from .notice import NoticeExtract
from .rules import KST

# (제출물에 이 말이 있으면, 단계 이름, 마감 전 남겨 둘 비율)
DELIV_STEPS = [
    (("readme", "배포", "실행"), "README·배포 정리", 0.18),
    (("기획서", "보고서", "계획서", "제안서"), "기획서·보고서 완성", 0.15),
    (("발표", "ppt", "슬라이드"), "발표 자료 완성", 0.12),
    (("영상", "시연", "데모"), "시연 영상 촬영", 0.08),
]


def _evening(dt: datetime, limit: datetime) -> datetime:
    """그날 밤 11시로 맞춘다. 마감보다 늦어지면 마감 3시간 전으로."""
    t = dt.astimezone(KST).replace(hour=23, minute=0, second=0, microsecond=0)
    return t if t <= limit - timedelta(hours=3) else limit - timedelta(hours=3)


def work_plan(ex: NoticeExtract, now: datetime | None = None) -> list[dict]:
    now = (now or datetime.now(KST)).astimezone(KST)
    if not ex.submit_deadline_iso:
        return []
    due = datetime.fromisoformat(ex.submit_deadline_iso).astimezone(KST)
    if due <= now:
        return []
    span = due - now
    names = " ".join(d.name.lower() for d in ex.deliverables)
    raw: list[tuple[str, datetime, str, str]] = []
    if ex.apply_deadline_iso:
        ap = datetime.fromisoformat(ex.apply_deadline_iso).astimezone(KST)
        if now < ap <= due:
            raw.append(("신청서 제출", ap, "apply", "신청 폼 제출은 직접 해 주세요"))
    if span > timedelta(days=2):
        raw += [("아이디어·역할 확정", _evening(now + span * 0.12, due), "step", "누가 무엇을 맡을지 이슈 담당자로 정해요"),
                ("핵심 기능 완성 (MVP)", _evening(now + span * 0.5, due), "step", "시연에 꼭 필요한 흐름 하나가 끝까지 돌아가게"),
                ("기능 마무리·버그 수정", _evening(now + span * 0.72, due), "step", "새 기능은 멈추고 다듬기")]
    for keys, title, frac in DELIV_STEPS:
        if any(k in names for k in keys):
            raw.append((title, _evening(due - span * frac, due), "step", "공고의 제출물"))
    raw.append(("최종 점검", due - max(timedelta(hours=3), span * 0.03), "step", "제출물 링크·README·권한(공개 여부) 확인"))
    raw.append(("제출 마감", due, "due", ex.submit_deadline or ""))
    seen, out = set(), []
    for title, at, kind, note in sorted(raw, key=lambda x: x[1]):
        if title in seen or (kind == "step" and at < now + timedelta(hours=1)):
            continue
        seen.add(title)
        out.append({"title": title, "due": at.isoformat(), "kind": kind, "note": note})
    return out
