"""신청·제출 직전 확인.

- 자동 확인: 기계가 확인할 수 있는 건 Bento가 직접 확인하고 근거(링크·커밋)를 붙인다.
- 사람 확인: 기계가 판단할 수 없는 것만 사람에게 묻는다.
- 버전 고정: 팀원 확인을 요청한 시점의 커밋을 저장소 이슈에 남기고, 그 뒤에 바뀐 게 있으면 알린다.
"""

import re
from datetime import datetime, timezone

from .fetch import probe
from .notice import KEYWORDS, Deliverable, _is_repo_item

CONFIRM_PREFIX = "[제출 확인]"
LOCK = re.compile(r"<!-- bento-lock:([0-9a-f]{7,40}) -->")
OK_WORDS = re.compile(r"확인|lgtm|ok\b|오케이|좋아요|👍|승인", re.I)
URL = re.compile(r"https?://[^\s)<>\]\"']+")


def _row(name, status, detail, evidence=None, fix=None):
    return {"name": name, "status": status, "detail": detail, "evidence": evidence or [], "fix": fix}


def _link_row(name, url, probe_fn):
    if not url:
        return _row(name, "fail", "링크가 아직 없어요", fix={"label": "제출 정보에 링크 넣기"})
    r = probe_fn(url)
    if r["ok"]:
        return _row(name, "ok", f"열려요 (응답 {r['status']})", [{"label": "링크 열기", "url": url}])
    return _row(name, "fail", r.get("error") or f"열리지 않아요 (응답 {r['status']})", [{"label": "링크 열기", "url": url}],
                {"label": "공개 범위나 주소를 확인해 주세요"})


def human_checks(deliverables: list[Deliverable], kind: str) -> list[dict]:
    if kind == "apply":
        return [{"id": "names", "text": "팀원 실명·학번(소속)을 직접 확인했어요"},
                {"id": "eligible", "text": "모든 팀원이 참가 자격을 충족해요"},
                {"id": "idea", "text": "신청서의 아이디어 한 줄을 팀원과 맞췄어요"},
                {"id": "privacy", "text": "개인정보 수집·이용 동의 내용을 직접 읽었어요"}]
    names = " ".join(d.name.lower() for d in deliverables)
    out = []
    if re.search(r"발표|ppt|슬라이드", names):
        out.append({"id": "slides", "text": "발표 자료가 최신 버전이고 지금 기능과 맞아요"})
    if re.search(r"영상|시연", names):
        out.append({"id": "video", "text": "시연 영상이 최신 기능을 보여 주고, 공개 범위가 '링크가 있는 사용자'예요"})
    out += [{"id": "agree", "text": "팀원 모두 이 버전으로 제출하는 데 동의했어요"},
            {"id": "form", "text": "제출 폼의 팀명·대표자 정보가 맞아요"}]
    return out


def find_confirm_issue(state) -> int | None:
    return next((i.number for i in state.issues if i.title.startswith(CONFIRM_PREFIX)), None)


def confirm_status(gh, number: int, members: list[str]) -> dict:
    iss = gh.issue(number)
    lock = LOCK.search(iss.get("body") or "")
    who = set()
    for r in gh.issue_reactions(number):
        if r.get("content") == "+1" and r["user"]["login"] in members:
            who.add(r["user"]["login"])
    proof = None
    for c in gh.issue_comments(number):
        login = c["user"]["login"]
        body = c.get("body") or ""
        if login in members and OK_WORDS.search(body):
            who.add(login)
        if m := re.search(r"제출 완료 증거: (.+)", body):
            proof = m.group(1).split("\n")[0].strip()
    closed = iss.get("state") == "closed"
    return {"issue": number, "url": iss.get("html_url"), "lock": lock.group(1) if lock else None,
            "confirmed_by": sorted(who), "closed": closed, "proof": proof if closed else None}


def run_checks(gh, state, deliverables: list[Deliverable], deadline_iso: str | None, links: dict[str, str],
               now: datetime | None = None, probe_fn=None) -> dict:
    now = now or datetime.now(timezone.utc)
    probe_fn = probe_fn or probe
    info = gh.repo_info()
    branch = info.get("default_branch") or "main"
    head = gh.head_sha(branch)
    commit_ev = {"label": f"커밋 {head[:7]} 기준", "url": gh.html(f"/commit/{head}")}
    rows = []
    names = " ".join(d.name for d in deliverables)

    rows.append(_row("저장소 공개", "fail" if info.get("private") else "ok",
                     "비공개 저장소예요. 심사위원이 볼 수 없어요" if info.get("private") else "누구나 볼 수 있어요",
                     [{"label": gh.repo, "url": gh.html()}],
                     {"label": "저장소 설정에서 공개로 바꾸기", "url": gh.html("/settings")} if info.get("private") else None))

    readme = gh.readme()
    readme_ev = [{"label": "README 보기", "url": gh.html(f"/blob/{branch}/README.md")}, commit_ev]
    if readme is None:
        rows.append(_row("README", "fail", "README가 없어요", fix={"label": "README 만들기", "url": gh.html(f"/new/{branch}?filename=README.md")}))
    else:
        run_ok = bool(re.search(r"실행|설치|시작하기|run|install|getting started|npm |pip |docker", readme, re.I))
        rows.append(_row("README 실행 방법", "ok" if run_ok else "warn", "실행 방법이 적혀 있어요" if run_ok else "실행 방법을 찾지 못했어요", readme_ev,
                         None if run_ok else {"label": "README 고치기", "url": gh.html(f"/edit/{branch}/README.md")}))

    deploy = links.get("deploy") or info.get("homepage") or ""
    if not deploy and readme:
        deploy = next((u for u in URL.findall(readme) if "github.com" not in u and "shields.io" not in u), "")
    if re.search(r"배포|데모|서비스\s*링크", names) or links.get("deploy"):
        r = _link_row("배포 링크 열림", deploy, probe_fn)
        if deploy and readme is not None and deploy not in readme:
            r["detail"] += " · README에는 이 링크가 없어요"
            r["status"] = "warn" if r["status"] == "ok" else r["status"]
        rows.append(r)
    if re.search(r"영상|시연", names) or links.get("video"):
        rows.append(_link_row("시연 영상 링크 열림", links.get("video"), probe_fn))
    if re.search(r"발표|ppt|슬라이드", names) or links.get("slides"):
        rows.append(_link_row("발표 자료 링크 열림", links.get("slides"), probe_fn))

    failing = [p for p in state.prs if p.ci == "fail"]
    rows.append(_row("열린 PR의 CI", "warn" if failing else "ok", "모두 통과" if not failing else ", ".join(f"PR #{p.number} 실패" for p in failing),
                     [{"label": f"PR #{p.number}", "url": p.url} for p in failing]))
    for d in deliverables:
        if _is_repo_item(d.name):
            continue
        kws = [k for k in KEYWORDS if k.lower() in d.name.lower()]
        mine = [i for i in state.issues if i.title == f"[제출물] {d.name}" or any(k.lower() in i.title.lower() for k in kws)]
        if mine:
            rows.append(_row(f"제출물 · {d.name}", "warn", ", ".join(f"#{i.number} 아직 열려 있음" for i in mine), [{"label": f"#{i.number}", "url": i.url} for i in mine]))

    if deadline_iso:
        from .notice import aware_iso
        try:
            due = datetime.fromisoformat(aware_iso(deadline_iso))
        except (ValueError, TypeError):  # 읽을 수 없는 날짜는 서버 오류(500) 대신 확인 필요로
            due = None
            rows.append(_row("마감까지 남은 시간", "warn", "마감 날짜를 읽지 못했어요. 공고에서 제출 마감을 확인해 주세요"))
        if due:
            left = (due - now).total_seconds() / 3600
            status = "fail" if left <= 0 else "warn" if left < 3 else "ok"
            rows.append(_row("마감까지 남은 시간", status, "마감이 지났어요" if left <= 0 else f"{int(left // 24)}일 {int(left % 24)}시간 남았어요"))

    lock = None
    if n := find_confirm_issue(state):
        st = confirm_status(gh, n, state.members)
        if st["lock"]:
            lock = st["lock"]
            if head.startswith(lock) or lock.startswith(head):
                rows.append(_row("확인 요청 뒤 바뀐 것", "ok", "팀원 확인을 요청한 버전 그대로예요", [{"label": f"#{n} 확인 요청", "url": st["url"]}, commit_ev]))
            else:
                diff = gh.compare(lock, head)
                rows.append(_row("확인 요청 뒤 바뀐 것", "fail", f"커밋 {diff['ahead_by']}개 · 바뀐 파일: {', '.join(diff['files'][:6]) or '없음'}",
                                 [{"label": "바뀐 부분 보기", "url": gh.html(f"/compare/{lock[:12]}...{head[:12]}")}],
                                 {"label": "바뀐 버전으로 팀원 확인을 다시 받아 주세요"}))
    return {"rows": rows, "head": {"sha": head, "short": head[:7], "url": commit_ev["url"], "branch": branch}, "lock": lock,
            "human": human_checks(deliverables, "submit"), "ready": not any(r["status"] == "fail" for r in rows),
            "deploy": deploy}
