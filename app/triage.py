"""규칙으로 막힌 곳을 찾고, 급한 순서로 정리해 행동 제안을 붙인다.

규칙이 근거(PR·이슈 링크)와 함께 후보를 찾고, 각 후보에는 고를 수 있는 행동 묶음을 1~2개 붙인다.
근거가 없는 경고는 만들지 않는다.
"""

import re
import shlex
from collections import Counter
from itertools import combinations

from .actions import AssignIssue, CloseIssue, Comment, CreateIssue, RequestReviewers, is_suspicious
from .state import RepoState

SEV_ORDER = {"긴급": 0, "주의": 1, "바로 가능": 2, "정리": 3, "확인": 4}


def issue_link(i, verb: str = "확인하러 가기", limit: int = 16) -> dict:
    """'이슈 #49'만으로는 뭘 하러 가는 버튼인지 모른다: 번호와 짧은 제목, 하러 가는 일을 같이 적는다."""
    title = re.sub(r"^\[[^\]]*\]\s*", "", i.title).strip()
    short = title if len(title) <= limit else title[:limit - 1] + "…"
    return {"label": f"#{i.number} {short} {verb}".replace("  ", " "), "url": i.url}


def _load(state: RepoState) -> Counter:
    """팀원별로 맡은 일(열린 이슈 담당 + 리뷰 요청) 수."""
    c = Counter({m: 0 for m in state.members})
    for i in state.issues:
        c.update(i.assignees)
    for p in state.prs:
        c.update(p.reviewers)
    return c


def _pick_reviewer(state: RepoState, pr) -> tuple[str | None, str]:
    load = _load(state)
    skip = {pr.author, *pr.reviewers}
    for f in pr.files:
        for who in state.file_authors.get(f, []):
            if who in state.members and who not in skip:
                return who, f"{f.split('/')[-1]}을 최근에 고친 적이 있어요"
    # 파일을 고친 사람이 없으면: 작성자와 같은 역할(예: 프론트엔드)인 팀원 → 맡은 일이 적은 사람
    mine = set(state.roles.get(pr.author, []))
    same = sorted((m for m in state.members if m not in skip and mine & set(state.roles.get(m, []))), key=lambda m: load[m])
    if same:
        from .ownership import role_names
        return same[0], f"작성자와 같은 {role_names(sorted(mine & set(state.roles.get(same[0], []))))} 담당이에요"
    cands = sorted((m for m in state.members if m not in skip), key=lambda m: load[m])
    return (cands[0], "지금 맡은 일이 가장 적어요") if cands else (None, "")


def triage(state: RepoState, review_wait_hours: float = 24) -> list[dict]:
    items: list[dict] = []
    gh_url = f"https://github.com/{state.repo}"
    load = _load(state)

    # 1. 리뷰가 오래 멈춘 PR
    for pr in state.prs:
        if pr.review_wait_hours is None or pr.review_wait_hours < review_wait_hours or pr.draft:
            continue
        who, why = _pick_reviewer(state, pr)
        options = []
        if who:
            options.append({"label": f"{who} 님께 리뷰 부탁하기", "recommended": True, "sub": why, "actions": [
                RequestReviewers(pr=pr.number, users=[who], expect_reviewers=list(pr.reviewers)),
                Comment(number=pr.number, body=f"@{who} 님, 리뷰를 {int(pr.review_wait_hours)}시간째 기다리고 있어서 부탁드려요. {why.replace('있어요', '있으세요')}!"),
            ]})
        if pr.reviewers:
            options.append({"label": "기존 리뷰어께 한 번 더 부탁하기", "sub": ", ".join(pr.reviewers), "actions": [
                Comment(number=pr.number, body=" ".join(f"@{r}" for r in pr.reviewers) + f" 님, 리뷰를 {int(pr.review_wait_hours)}시간째 기다리고 있어요. 바쁘시면 편하게 말씀해 주세요!"),
            ]})
        items.append({"id": f"review-{pr.number}", "sev": "긴급", "title": f"PR #{pr.number} 리뷰가 {int(pr.review_wait_hours)}시간째 멈춤",
                      "why": f"{pr.title} · 리뷰가 밀리면 머지와 배포가 함께 밀려요", "evidence": [{"label": f"PR #{pr.number}", "url": pr.url}], "options": options})

    # 2. 담당자 없는 이슈 (마일스톤이 있으면 그 안에서만)
    from .notice import is_submit_act
    has_ms = any(i.milestone for i in state.issues)
    unassigned = [i for i in state.issues if not i.assignees and not is_suspicious(i.body) and (i.milestone or not has_ms)
                  and not is_submit_act(re.sub(r"^\[[^\]]*\]\s*", "", i.title)) and not i.title.startswith("[승인 요청]")]  # 예전에 만든 '[제출] 제출 폼 작성'은 나눌 일이 아니다
    solo = len(state.members) <= 1  # 혼자인 저장소는 다 내 일이라 나누자고 하지 않는다
    if unassigned and not solo:
        from .ownership import named_owner, pick_by_role
        pairs, given, asked = [], dict(load), []
        for i in unassigned:  # 제목에 담당이 적혀 있으면 그 사람, 아니면 역할 → 맡은 일이 적은 순서
            who = named_owner(i.title, state.members, getattr(state, "names", None), state.leads)
            if who == "?":
                asked.append(i)  # '담당: A·발표' 처럼 누군지 모르는 건 팀장이 직접 정하게 둔다
                continue
            who = who or pick_by_role(state.members, state.roles, given, i.title)[0]
            if who:
                given[who] = given.get(who, 0) + 1
                pairs.append((i, who))
        # 한두 개면 이유를 댓글로 남기고, 여러 개면 담당자 지정 알림만으로 충분하다 (댓글 폭탄 막기)
        acts = [AssignIssue(issue=i.number, users=[u], expect_assignees=list(i.assignees)) for i, u in pairs]
        if len(pairs) <= 3:
            acts += [Comment(number=i.number, body=f"@{u} 님, 이 일을 맡아 주실 수 있을까요? 어려우면 편하게 말씀해 주세요!") for i, u in pairs]
        sub = " · ".join(f"#{i.number} → {u}" for i, u in pairs[:4])
        if asked:
            sub += (" · " if sub else "") + f"제목에 담당이 적힌 {len(asked)}개({', '.join('#' + str(i.number) for i in asked[:3])}{'…' if len(asked) > 3 else ''})는 직접 정해 주세요"
        items.append({"id": "assign", "sev": "긴급" if _due_soon(state, unassigned) else "주의", "title": f"담당자 없는 이슈 {len(unassigned)}개",
                      "why": "맡은 사람이 없는 이슈예요. 아래 이슈를 눌러 내용을 확인하고, 괜찮으면 Bento가 나눠 드려요" + (f" (외 {len(unassigned) - 4}개)" if len(unassigned) > 4 else ""),
                      "evidence": [issue_link(i) for i in unassigned[:4]],
                      "options": [{"label": "역할대로 나누기" if state.roles else "맡은 일이 적은 순서로 나누기", "recommended": True,
                                   "sub": sub, "actions": acts}] if pairs else []})

    # 3. 같은 파일을 동시에 고치는 PR
    for a, b in combinations(state.prs, 2):
        shared = sorted(set(a.files) & set(b.files))
        if not shared or any(f"#{a.number}" in i.title and f"#{b.number}" in i.title for i in state.issues):
            continue
        first, second = (a, b) if a.approvals else (b, a)
        title = f"{shared[0].split('/')[-1]} 머지 순서 정하기 (#{first.number} → #{second.number})"
        items.append({"id": f"conflict-{a.number}-{b.number}", "sev": "주의", "title": f"{shared[0].split('/')[-1]}을 PR 두 개가 같이 고치는 중",
                      "why": f"#{a.number}({a.author})와 #{b.number}({b.author}) · 순서를 안 정하면 머지할 때 충돌이 나요",
                      "evidence": [{"label": f"PR #{a.number}", "url": a.url}, {"label": f"PR #{b.number}", "url": b.url}],
                      "options": [{"label": "머지 순서 이슈 만들고 두 분께 알리기", "recommended": True, "sub": f"#{first.number}을 먼저 머지하는 안", "actions": [
                          CreateIssue(title=title, body=f"#{a.number}과 #{b.number}이 `{', '.join(shared)}`을 함께 고치고 있어요.\n승인이 먼저 난 #{first.number}을 먼저 머지하고, #{second.number}이 그 위에서 이어 가는 걸 제안해요.", assignees=sorted({a.author, b.author})),
                      ]}]})

    # 4. CI 실패 · 충돌 난 PR → 코드는 사람이, Bento는 안내와 연락
    for pr in state.prs:
        if pr.ci == "fail" or pr.mergeable == "dirty":
            why = []
            if pr.ci == "fail":
                why.append(f"CI 실패{f' {int(pr.ci_fail_hours)}시간째' if pr.ci_fail_hours else ''}")
            if pr.mergeable == "dirty":
                why.append("main과 충돌")
            items.append({"id": f"fix-{pr.number}", "sev": "주의", "title": f"PR #{pr.number} {' · '.join(why)}",
                          "why": "코드는 직접 고치셔야 해요. 바로 볼 수 있게 링크와 명령어를 준비했어요",
                          "evidence": [{"label": f"PR #{pr.number}", "url": pr.url}],
                          "handoff": [
                              {"label": "CI 기록 보기", "url": f"{pr.url}/checks"} if pr.ci == "fail" else None,
                              {"label": "바뀐 파일 보기", "url": f"{pr.url}/files"},
                              {"label": "브랜치 받기", "command": f"git fetch origin && git checkout {shlex.quote(pr.branch)}"},
                              {"label": "main 최신 내용 합치기", "command": "git merge origin/main"} if pr.mergeable == "dirty" else None,
                          ],
                          "options": [{"label": f"{pr.author} 님께 상황 묻기", "recommended": True, "sub": "PR 댓글", "actions": [
                              Comment(number=pr.number, body=f"@{pr.author} 님, 이 PR이 {', '.join(why)} 상태예요. 막힌 부분이 있으면 알려 주세요. 같이 볼 사람을 찾아 드릴게요!")]}]})

    # 5. 머지할 준비가 된 PR → 머지는 사람이
    for pr in state.prs:
        if pr.approvals and pr.ci in ("pass", "none") and pr.mergeable == "clean":
            items.append({"id": f"merge-{pr.number}", "sev": "바로 가능", "title": f"PR #{pr.number}은 머지할 준비가 됨",
                          "why": f"{', '.join(pr.approvals)} 님 승인 · CI {'통과' if pr.ci == 'pass' else '없음'} · 충돌 없음. 머지 버튼은 직접 눌러 주세요",
                          "evidence": [{"label": f"PR #{pr.number}", "url": pr.url}],
                          "handoff": [{"label": f"PR #{pr.number} 머지하러 가기", "url": pr.url}],
                          "options": [{"label": f"{pr.author} 님께 머지해도 된다고 알리기", "recommended": True, "sub": "PR 댓글", "actions": [
                              Comment(number=pr.number, body=f"@{pr.author} 님, 리뷰 승인과 CI 확인이 끝나서 머지할 수 있어요!")]}]})

    # 6. 머지된 PR이 언급했는데 아직 열린 이슈
    for i in state.issues:
        if i.number in state.merged_refs and not is_suspicious(i.body):
            pr_n = state.merged_refs[i.number]
            items.append({"id": f"close-{i.number}", "sev": "정리", "title": f"이슈 #{i.number}이 끝났는데 열려 있음",
                          "why": f"{i.title} · PR #{pr_n}이 머지되면서 언급했어요. 남은 일이 없다면 닫아요",
                          "evidence": [{"label": f"이슈 #{i.number}", "url": i.url}, {"label": f"PR #{pr_n}", "url": f"{gh_url}/pull/{pr_n}"}],
                          "options": [{"label": f"이유 남기고 #{i.number} 닫기", "recommended": True, "sub": "다시 열 수 있어요", "actions": [
                              CloseIssue(issue=i.number, comment=f"PR #{pr_n}이 머지되어 끝난 것으로 보고 닫아요. 남은 일이 있으면 다시 열어 주세요!")]}]})

    # 7. 에이전트에게 하는 의심 지시
    for i in state.issues:
        if is_suspicious(i.body):
            items.append({"id": f"flag-{i.number}", "sev": "확인", "title": f"이슈 #{i.number}에 의심 지시가 있음",
                          "why": f"'{i.body[:60]}' · 따르지 않았고, 이 이슈는 건드리지 않아요. 팀이 직접 읽어 봐 주세요",
                          "evidence": [{"label": f"이슈 #{i.number}", "url": i.url}],
                          "handoff": [{"label": f"이슈 #{i.number} 직접 확인하기", "url": i.url}], "options": []})

    # 8~. PR·이슈 밖의 일: main 빌드·비밀키·큰 파일·조용한 팀원·초대·수정 요청·혼자 머지·계정 연결·마감
    from .watch import watch
    items += watch(state)

    for it in items:
        it["handoff"] = [h for h in it.get("handoff", []) if h]
    return sorted(items, key=lambda it: SEV_ORDER.get(it["sev"], 9))


def _due_soon(state, issues, days: float = 3) -> bool:
    """마감이 3일 안이거나 이미 지난 마일스톤에 들어 있을 때만 긴급."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for i in issues:
        m = state.milestone(i.milestone) if i.milestone else None
        due = m.due_on if m else None
        if isinstance(due, str):
            due = datetime.fromisoformat(due.replace("Z", "+00:00"))
        if due and (due - now).total_seconds() < days * 86400:
            return True
    return False
