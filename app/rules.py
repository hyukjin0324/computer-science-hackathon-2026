"""AI 키가 없을 때 공고를 규칙으로 읽는다.

정확도는 AI보다 낮지만 토큰이 들지 않는다. 날짜·제출물 줄을 찾아 그 줄을 그대로 근거로 붙인다.
"""

import re
from datetime import datetime, timedelta, timezone

from .notice import Deliverable, Fact, NoticeExtract

KST = timezone(timedelta(hours=9))
WD = "월화수목금토일"
DATE = re.compile(
    r"(?:(20\d{2})\s*[.\-/년]\s*)?(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?\.?"
    r"(?:\s*\(\s*[월화수목금토일]\s*\))?"
    r"(?:\s*(오전|오후)?\s*(\d{1,2})\s*(?::\s*(\d{2})|시(?:\s*(\d{1,2})\s*분)?))?"
)
APPLY = re.compile(r"신청|접수|모집|참가\s*등록|등록\s*마감")
SUBMIT = re.compile(r"제출|마감|최종")
DELIV_LABEL = re.compile(r"제출\s*(물|서류|자료|항목)")
BULLET = re.compile(r"^\s*(?:[-•·*▪◦]|\d+[.)]|[①-⑳])\s*")
FACT_LABELS = [("참가 자격", re.compile(r"자격|대상")), ("심사 기준", re.compile(r"심사|평가")), ("시상", re.compile(r"시상|상금|혜택")),
               ("주제", re.compile(r"^\s*주제")), ("진행 기간", re.compile(r"기간|일정"))]
TITLE_HINT = re.compile(r"대회|해커톤|공모전|경진|챌린지|해카톤|아이디어톤|hackathon|contest", re.I)


def _heading(line: str) -> str:
    """'[참가 자격]', '■ 참가 대상', '참가 자격' 처럼 내용 없이 제목만 있는 줄이면 그 제목."""
    t = line.strip()
    if m := re.match(r"^[\[【<■□▶◆●○#\s]*([가-힣A-Za-z ]{2,12}?)\s*[\]】>]?\s*$", t):
        return m.group(1).strip()
    return ""


def _label(line: str) -> tuple[str, str]:
    """'신청: 11/18까지' → ('신청', '11/18까지'). 라벨이 없으면 ('', 줄)."""
    m = re.match(r"^\s*[\[【<(]?\s*([^:：\]】>)]{1,14}?)\s*[\]】>)]?\s*[:：]\s*(.*)$", line)
    return (m.group(1).strip(), m.group(2).strip()) if m else ("", line.strip())


def _dates(line: str, now: datetime) -> list[datetime]:
    out = []
    for m in DATE.finditer(line):
        y, mo, d, ampm, hh, mm, mm2 = m.groups()
        mo, d = int(mo), int(d)
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        year = int(y) if y else now.year
        h, mi = (int(hh), int(mm or mm2 or 0)) if hh else (23, 59)
        if ampm == "오후" and h < 12:
            h += 12
        if h > 23 or mi > 59:
            h, mi = 23, 59
        try:
            dt = datetime(year, mo, d, h, mi, tzinfo=KST)
        except ValueError:
            continue
        if not y and dt < now - timedelta(days=180):  # 연도가 없고 한참 지났으면 내년 일정
            dt = dt.replace(year=year + 1)
        out.append(dt)
    return out


def human(dt: datetime) -> str:
    return f"{dt.month}월 {dt.day}일({WD[dt.weekday()]}) {dt:%H:%M}"


def _title(lines: list[str]) -> str:
    head = [l for l in lines[:20] if not re.search(r"\$\{|\{\{|=>|\bfunction\b", l)][:12]  # 코드 조각은 제목이 될 수 없다
    pick = next((l for l in head if TITLE_HINT.search(l) and len(l) <= 80), head[0] if head else "공고")
    pick = re.sub(r"^\s*(\[[^\]]{1,12}\]|【[^】]{1,12}】)\s*", "", pick)
    return _label(pick)[1][:60] if ":" in pick[:15] else pick[:60]


def _team(lines: list[str]) -> tuple[int | None, int | None]:
    for l in lines:
        if not re.search(r"팀|인원|자격|구성|대상", l):
            continue
        if m := re.search(r"(\d+)\s*[~\-–]\s*(\d+)\s*(?:인|명)", l):
            return int(m.group(1)), int(m.group(2))
        lo = re.search(r"(\d+)\s*(?:인|명)\s*이상", l)
        hi = re.search(r"(?:최대\s*)?(\d+)\s*(?:인|명)\s*(?:이하|이내|까지)", l) or re.search(r"최대\s*(\d+)\s*(?:인|명)", l)
        if lo or hi:
            return (int(lo.group(1)) if lo else None), (int(hi.group(1)) if hi else None)
    return None, None


def _split_items(s: str) -> list[str]:
    parts = [p for p in re.split(r"\s*[①-⑳]\s*", s) if p.strip()]
    if len(parts) <= 1:
        parts = re.split(r"\s*,\s*|\s*·\s*|\s+및\s+", s)
    return [p.strip(" .;") for p in parts if 1 < len(p.strip(" .;")) <= 60]


def rule_extract(text: str, now: datetime | None = None) -> NoticeExtract:
    now = now or datetime.now(KST)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    facts: list[Fact] = []
    apply_hits: list[tuple[datetime, str]] = []
    submit_hits: list[tuple[datetime, str]] = []
    delivs: list[Deliverable] = []
    fields: list[str] = []
    for i, line in enumerate(lines):
        lab, body = _label(line)
        key = lab or line
        ds = _dates(line, now)
        if ds and not DELIV_LABEL.search(lab):
            if APPLY.search(lab) or (not lab and APPLY.search(line)):
                apply_hits.append((max(ds), line))
                if (m := re.search(r"\(([^()]{2,60})\)", re.sub(r"\(\s*[월화수목금토일]\s*\)", "", line))) and "," in m.group(1):
                    fields = [f.strip() for f in m.group(1).split(",") if 0 < len(f.strip()) <= 15]
            elif SUBMIT.search(key) and not re.search(r"기간", lab):
                submit_hits.append((max(ds), line))
        if DELIV_LABEL.search(lab):
            items = _split_items(body) if body else []
            if not items:  # 다음 줄들이 목록인 경우
                for nxt in lines[i + 1:i + 9]:
                    if not BULLET.match(nxt) or _label(BULLET.sub("", nxt))[0]:
                        break
                    items.append(BULLET.sub("", nxt).strip(" .;"))
            delivs += [Deliverable(name=it, quote=it) for it in items if it]
        for name, rx in FACT_LABELS:
            if name == "진행 기간" and (APPLY.search(lab) or SUBMIT.search(lab)):
                continue
            if lab and rx.search(lab) and body and not any(f.label == name for f in facts):
                facts.append(Fact(label=name, value=body[:120], quote=line[:120]))
            elif not lab and (hd := _heading(line)) and rx.search(hd) and not any(f.label == name for f in facts):
                # '[참가 자격]' 같은 제목 줄 다음 줄에 내용이 오는 공고
                nxt = next((x.strip() for x in lines[i + 1:i + 4] if x.strip()), "")
                if nxt and not _heading(nxt):
                    facts.append(Fact(label=name, value=BULLET.sub("", nxt).strip()[:120], quote=nxt[:120]))
    apply = max(apply_hits, default=None)
    submit = max(submit_hits, default=None)
    if apply:
        facts.insert(1 if facts else 0, Fact(label="신청 마감", value=human(apply[0]), quote=apply[1][:120]))
    if submit:
        facts.insert(2 if len(facts) > 1 else len(facts), Fact(label="제출 마감", value=human(submit[0]), quote=submit[1][:120]))
    tmin, tmax = _team(lines)
    return NoticeExtract(
        title=_title(lines), facts=facts, team_min=tmin, team_max=tmax, apply_fields=fields,
        apply_deadline=human(apply[0]) if apply else None, apply_deadline_iso=apply[0].isoformat() if apply else None,
        submit_deadline=human(submit[0]) if submit else None, submit_deadline_iso=submit[0].isoformat() if submit else None,
        deliverables=delivs[:10],
    )
