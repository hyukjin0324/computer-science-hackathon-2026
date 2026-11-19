"""AI 키가 없을 때 공고를 규칙으로 읽는다.

정확도는 AI보다 낮지만 토큰이 들지 않는다. 날짜·제출물 줄을 찾아 그 줄을 그대로 근거로 붙인다.
"""

import re
from datetime import datetime, timedelta, timezone

from .notice import Deliverable, Fact, NoticeExtract

KST = timezone(timedelta(hours=9))
WD = "월화수목금토일"
DATE = re.compile(
    r"(?<![\d.])(?<![Vv])(?<!(?i:python)\s)(?<!(?i:ver)\s)(?<!(?i:version)\s)"  # 'Python 3.11', 'v1.2'는 버전이지 날짜가 아니다 (그 밖의 영문 뒤 'Deadline 11.24'는 날짜)
    r"(?:(20\d{2}|\d{2})\s*[.\-/년]\s*)?(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?\.?"
    r"(?:\s*\(\s*[월화수목금토일]\s*\))?"
    r"(?:\s*(오전|오후)?\s*(\d{1,2})\s*(?::\s*(\d{2})|시(?:\s*(\d{1,2})\s*분)?))?"
    r"(?!\d|\s*(?:[KMG]B|%|명|팀)|\s*(?:억|만\s*원)|\s*(?:원|배|점|세)(?![가-힣]))"  # '1.5억', '2.5배', '4.5점'은 날짜가 아니다
)
# 연도·월·일·요일·시각 없이 '1.5'처럼 점 하나로만 이은 숫자: 같은 줄에 확실한 날짜가 있으면 날짜로 보지 않는다
_STRONG = re.compile(r"년|월|일|\(|:|시")
APPLY = re.compile(r"신청|접수|모집|참가\s*등록|등록\s*마감")
SUBMIT = re.compile(r"제출|마감")
NOT_SUBMIT = re.compile(r"발표(?!\s*(?:자료|영상|파일|ppt|PPT))|시상|심사|결과\s*(?:안내|통보)|오리엔|교육|오픈")  # '발표 자료 제출'은 제출이다
MAX_LINE = 200  # 후기·본문 속 긴 문장은 일정 줄이 아니다
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
    found = list(DATE.finditer(line))
    strong = [m for m in found if m.group(1) or _STRONG.search(m.group(0))]
    for m in found:
        if strong and m not in strong:  # '11월 30일 18:00 … 상금 1.5억' → 1.5는 날짜가 아니다
            continue
        y, mo, d, ampm, hh, mm, mm2 = m.groups()
        mo, d = int(mo), int(d)
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        year = (int(y) if len(y) == 4 else 2000 + int(y)) if y else now.year
        h, mi = (int(hh), int(mm or mm2 or 0)) if hh else (23, 59)
        if ampm == "오후" and h < 12:
            h += 12
        elif ampm == "오전" and h == 12:  # 오전 12시 = 자정(0시)
            h = 0
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
        if re.search(r"개인", l) and re.search(r"(?:팀|2\s*인|단체).{0,12}불가", l):  # '개인 (2인 이상 팀 단위 참여 불가)'
            return 1, 1
        if not re.search(r"팀|인원|자격|구성|대상", l) or re.search(r"규모|\d+\s*팀\s*\(|약\s*\d+\s*명|총\s*\d+\s*명", l):
            continue
        if m := re.search(r"(\d+)\s*[~\-–]\s*(\d+)\s*(?:인|명)", l):
            return int(m.group(1)), int(m.group(2))
        lo = re.search(r"(\d+)\s*(?:인|명)\s*이상", l)
        hi = re.search(r"(?:최대\s*)?(\d+)\s*(?:인|명)\s*(?:이하|이내|까지)", l) or re.search(r"최대\s*(\d+)\s*(?:인|명)", l)
        if lo or hi:
            return (int(lo.group(1)) if lo else None), (int(hi.group(1)) if hi else None)
    return None, None


_SEP = re.compile(r"\s*,\s*|\s+·\s+|\s+및\s+")


def _split_top(s: str) -> list[str]:
    """쉼표·'및'·'·'로 나누되 괄호 안은 나누지 않는다. '발표 자료(PDF 또는 PPTX, 10MB 이하)'가 둘로 갈라지지 않게."""
    parts, buf, depth, i = [], [], 0, 0
    while i < len(s):
        ch = s[i]
        if ch in "([（":
            depth += 1
        elif ch in ")]）":
            depth = max(0, depth - 1)
        if depth == 0 and (m := _SEP.match(s, i)) and m.end() > i:
            parts.append("".join(buf))
            buf, i = [], m.end()
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _split_items(s: str) -> list[str]:
    parts = [p for p in re.split(r"\s*[①-⑳]\s*", s) if p.strip()]
    if len(parts) <= 1:
        parts = _split_top(s)
    return [p.strip(" .;") for p in parts if 1 < len(p.strip(" .;")) <= 60]


MERGED = "\u200b"  # 제목 줄과 합친 줄 표시(다른 줄에서 찾은 마감이 있으면 그쪽을 우선한다)
HEAD_ONLY = re.compile(r"^(?:신청|접수|모집|제출|참가\s*신청)\s*(?:기간|마감|일정)?$")


def _merge_headings(lines: list[str]) -> list[str]:
    """포스터처럼 '모집기간' 제목 줄 아래 줄에 날짜가 오는 경우: '모집기간: ~ 11월 20일 (금)'으로 합친다. ('접 수'처럼 띄어 쓴 제목도 본다)"""
    out, i = [], 0
    while i < len(lines):
        head = re.sub(r"\s+", "", lines[i]) if len(lines[i]) <= 12 else ""
        if head and HEAD_ONLY.match(head) and i + 1 < len(lines) and DATE.search(lines[i + 1]) and len(lines[i + 1]) <= 80:
            out.append(f"{head}: {lines[i + 1]}{MERGED}")
            i += 2
        else:
            out.append(lines[i])
            i += 1
    return out


def _quote(line: str) -> str:
    """합친 줄은 원문에 없는 글자이므로, 근거 인용은 날짜가 있던 원래 줄만 쓴다."""
    return (line.replace(MERGED, "").split(": ", 1)[-1] if line.endswith(MERGED) else line)[:120]


def rule_extract(text: str, now: datetime | None = None) -> NoticeExtract:
    now = now or datetime.now(KST)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    lines = _merge_headings(lines)
    facts: list[Fact] = []
    apply_hits: list[tuple[datetime, str]] = []
    submit_hits: list[tuple[datetime, str]] = []
    delivs: list[Deliverable] = []
    fields: list[str] = []
    for i, line in enumerate(lines):
        lab, body = _label(line)
        key = lab or line
        ds = _dates(line, now) if len(line) <= MAX_LINE else []
        if ds and not DELIV_LABEL.search(lab):
            if APPLY.search(lab) or (not lab and APPLY.search(line)):
                apply_hits.append((max(ds), line))
                if (m := re.search(r"\(([^()]{2,60})\)", re.sub(r"\(\s*[월화수목금토일]\s*\)", "", line))) and "," in m.group(1):
                    fields = [f.strip() for f in m.group(1).split(",") if 0 < len(f.strip()) <= 15]
            elif SUBMIT.search(key) and not (re.search(r"기간", lab) and not re.search(r"제출\s*기간", lab)) and not NOT_SUBMIT.search(key):  # '제출 기간: A ~ B'의 B는 제출 마감
                submit_hits.append((max(ds), line))
        if DELIV_LABEL.search(lab):
            items = _split_items(body) if body else []
            if not items:  # 다음 줄들이 목록인 경우
                for nxt in lines[i + 1:i + 9]:
                    if not BULLET.match(nxt) or _label(BULLET.sub("", nxt))[0]:
                        break
                    items.append(BULLET.sub("", nxt).strip(" .;"))
            delivs += [Deliverable(name=it, quote=it) for it in items if it]
        if (m := re.search(r"\(\s*제출\s*(?:물|서류|자료|항목|파일)\s*\)\s*(.*)$", line)) and (c := re.search(r"[①-⑳]", m.group(1))):  # '○ (제출 파일) ...-①A ②B'
            delivs += [Deliverable(name=re.sub(r"\s*(?:하여\s*)?제출[-\s]*$", "", it.replace("『", "").replace("』", " ")).strip(), quote=it) for it in _split_items(m.group(1)[c.start():])]
        if not lab and len(line) <= MAX_LINE and (m := re.match(r"^[※*▶●○\-•\s]*(.{3,120}?)\s*(?:을|를)?\s*(?:모두\s*)?제출\s*(?:하|해|합|\.|$)", line)) \
                and not DATE.search(line) and "," in m.group(1):
            delivs += [Deliverable(name=it, quote=it) for it in _split_items(m.group(1))]
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
    official = [h for h in apply_hits if re.match(r"^사이트 접수기간\s*:", h[1])]  # 사이트 정보 칸의 접수기간이 있으면 본문 속 날짜보다 우선
    plain = [h for h in apply_hits if not h[1].endswith(MERGED)]
    apply = max(official or plain or apply_hits, default=None)
    if apply and official:  # 정보 칸에는 시각이 없으니, 같은 날짜가 시각과 함께 적힌 본문 줄이 있으면 그 시각을 쓴다
        timed = next((h for h in apply_hits if h[0].date() == apply[0].date() and re.search(r"\d{1,2}\s*(?::\s*\d{2}|시)", h[1])), None)
        if timed:
            apply = (timed[0], apply[1])
    submit = max([h for h in submit_hits if not h[1].endswith(MERGED)] or submit_hits, default=None)
    if apply:
        facts.insert(1 if facts else 0, Fact(label="신청 마감", value=human(apply[0]), quote=_quote(apply[1])))
    if submit:
        facts.insert(2 if len(facts) > 1 else len(facts), Fact(label="제출 마감", value=human(submit[0]), quote=_quote(submit[1])))
    tmin, tmax = _team(lines)
    return NoticeExtract(
        title=_title(lines), facts=facts, team_min=tmin, team_max=tmax, apply_fields=fields,
        apply_deadline=human(apply[0]) if apply else None, apply_deadline_iso=apply[0].isoformat() if apply else None,
        submit_deadline=human(submit[0]) if submit else None, submit_deadline_iso=submit[0].isoformat() if submit else None,
        deliverables=delivs[:10],
    )
