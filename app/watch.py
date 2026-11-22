"""PR·이슈 말고 팀플에서 자주 터지는 일을 규칙으로 찾는다. 근거(커밋·PR 링크) 없이는 경고하지 않는다.

- main 빌드 깨짐 · 비밀키가 올라감 · 큰 파일/올리면 안 되는 폴더
- 연락이 끊긴 팀원 · 맡기만 하고 손을 안 댄 할 일 · 초대를 수락하지 않은 팀원
- 수정 요청에 답이 없는 PR · 리뷰 승인 없이 머지된 PR · 계정에 연결되지 않은 커밋
- 제출 마감이 가까움 · 마감 뒤에 올라온 커밋

코드를 고치거나 푸시하지 않는다. 고치는 방법은 안내(handoff)로, Bento가 할 수 있는 건 댓글·이슈 제안으로만 둔다.
"""

import re
import shlex
from datetime import datetime, timedelta

from .actions import Comment, CreateIssue, is_suspicious
from .state import RepoState, hours, ms_due, parse_ts
from .triage import issue_link

QUIET_H = 72  # 이만큼 활동이 없으면 '연락이 끊긴 팀원'
STALE_H = 72  # 맡은 할 일이 이만큼 그대로면 '손을 안 댄 할 일'
CHANGES_H = 24  # 수정 요청 뒤 이만큼 새 커밋이 없으면
INVITE_H = 12
SUBMIT_MS = "해커톤 제출"


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.0f}MB"


def deadline_of(state: RepoState) -> datetime | None:
    m = state.milestone(SUBMIT_MS)
    return ms_due(m.due_on) if m and m.due_on else None


def watch(state: RepoState) -> list[dict]:
    sig, now, items = state.signals or {}, state.now, []
    gh_url = f"https://github.com/{state.repo}"
    team = len(state.members) > 1
    branch = sig.get("branch") or "main"
    lead = state.leads[0] if state.leads else None  # 팀 전체의 일은 팀장 몫으로

    # 1. main 빌드가 깨짐
    main = sig.get("main") or {}
    if main.get("ci") == "fail":
        who = main.get("author")
        acts = [CreateIssue(title=f"{branch} 빌드 고치기", body=f"{branch}의 최근 커밋 ({main.get('message', '')})에서 CI가 실패했어요.\n{main.get('url', '')}\n고칠 때까지 새 머지를 잠깐 멈추는 걸 추천해요.",
                            assignees=[who] if who in state.members else [])]
        items.append({"id": "main-ci", "sev": "긴급", "who": who if who in state.members else None, "age": main.get("fail_hours") or 0,
                      "title": f"{branch} 빌드가 깨졌어요" + (f" · {int(main['fail_hours'])}시간째" if main.get("fail_hours") else ""),
                      "why": (f"{who} 님의 마지막 커밋 '{main.get('message', '')}' 뒤로 CI가 실패해요. " if who else "") + "모두의 브랜치가 막히니 먼저 고쳐야 해요",
                      "evidence": [{"label": "실패한 커밋", "url": main.get("url") or f"{gh_url}/commits/{branch}"}],
                      "handoff": [{"label": "CI 기록 보기", "url": f"{main.get('url', gh_url)}/checks" if main.get("url") else f"{gh_url}/actions"},
                                  {"label": "최신 main 받기", "command": f"git checkout {shlex.quote(branch)} && git pull origin {shlex.quote(branch)}"}],
                      "options": [{"label": "'빌드 고치기' 할 일 만들기" + (f" ({who} 님 담당)" if who in state.members else ""), "recommended": True, "sub": "GitHub 이슈", "actions": acts}]})

    # 2. 비밀키가 올라감 (값은 절대 보여 주지 않는다)
    tree = sig.get("tree") or {}
    leaks = [{"path": p, "kinds": ["비밀 설정 파일"]} for p in tree.get("secrets", [])] + list(sig.get("keys") or [])
    if leaks:
        first = leaks[0]
        who = first.get("login")
        names = ", ".join(sorted({l["path"] for l in leaks})[:3])
        items.append({"id": "secret", "sev": "긴급", "who": who if who in state.members else None, "age": 0,
                      "title": f"비밀키가 저장소에 올라갔을 수 있어요 · {names}",
                      "why": f"{' · '.join(sorted({k for l in leaks for k in l['kinds']}))}. 파일을 지워도 기록에 남아요. "
                             "먼저 그 키를 발급한 곳에서 폐기하고 새로 받아 주세요",
                      "evidence": [{"label": l["path"], "url": l.get("url") or f"{gh_url}/blob/{branch}/{l['path']}"} for l in leaks[:3]],
                      "handoff": [{"label": "1. 키 폐기하고 새로 발급받기", "url": "https://docs.github.com/ko/code-security/secret-scanning/working-with-secret-scanning-and-push-protection"},
                                  ({"label": "2. 비밀 파일을 저장소에서 빼기", "command": " && ".join(f"git rm --cached {shlex.quote(p)}" for p in tree.get("secrets", [])[:3])} if tree.get("secrets") else None),
                                  ({"label": "2. 코드에 적힌 키는 지우고 환경 변수(.env)로 옮기기", "url": (sig.get("keys") or [{}])[0].get("url") or gh_url} if sig.get("keys") else None),
                                  {"label": "3. 다시 안 올라가게", "command": "echo '.env*' >> .gitignore && echo '!.env.example' >> .gitignore"}],
                      "options": []})  # 공개 저장소에 '키가 여기 있다'고 적지 않도록 댓글·이슈는 만들지 않는다

    # 3. 큰 파일 · 올리면 안 되는 폴더
    if tree.get("big") or tree.get("junk"):
        parts = [f"{b['path'].split('/')[-1]} ({_mb(b['size'])})" for b in tree.get("big", [])[:2]] + [f"{j}/ 폴더" for j in tree.get("junk", [])[:2]]
        cmds = [f"git rm -r --cached {j} && echo '{j}/' >> .gitignore" for j in tree.get("junk", [])[:2]]
        cmds += [f"git rm --cached {shlex.quote(b['path'])}" for b in tree.get("big", [])[:2]]  # 파일 이름에 따옴표·세미콜론이 있어도 명령이 되지 않게
        items.append({"id": "bigfile", "sev": "정리", "who": lead, "age": 0, "title": f"저장소에 무거운 파일이 있어요 · {', '.join(parts)}",
                      "why": "저장소가 무거워지고 100MB가 넘으면 푸시가 막혀요. 영상·데이터는 드라이브 링크로, 설치 폴더는 .gitignore로 빼요",
                      "evidence": [{"label": b["path"], "url": f"{gh_url}/blob/{branch}/{b['path']}"} for b in tree.get("big", [])[:2]] or [{"label": "저장소 파일", "url": gh_url}],
                      "handoff": [{"label": "저장소에서 빼기", "command": " && ".join(cmds)}] if cmds else [], "options": []})

    # 4. 연락이 끊긴 팀원: 다른 팀원은 움직이는데 혼자 3일 넘게 조용함
    act = sig.get("activity")
    if team and act is not None:
        recent = [m for m in state.members if (t := parse_ts(act.get(m))) and hours(t, now) < QUIET_H]
        if recent:
            for m in state.members:
                if m in state.leads:
                    continue  # 어느 팀장이 승인하더라도 자기 자신에게 안부 댓글을 보내지 않게 팀장은 대상에서 뺀다
                t = parse_ts(act.get(m))
                if t and hours(t, now) < QUIET_H:
                    continue
                mine = [i for i in state.issues if m in i.assignees and not is_suspicious(i.body)]
                prs = [p for p in state.prs if p.author == m]
                if not mine and not prs:
                    continue  # 맡은 일이 없으면 조용해도 괜찮다
                since = f"{int(hours(t, now) // 24)}일째 활동이 없어요" if t else "저장소에서 활동한 기록이 없어요"
                target = mine[0].number if mine else prs[0].number
                items.append({"id": f"quiet-{m}", "sev": "주의", "who": lead, "age": 0,
                              "title": f"{m} 님이 {since.replace('어요', '음')}",
                              "why": f"맡은 할 일 {len(mine)}개 · 열린 PR {len(prs)}개. 바쁜 사정이 있는지 먼저 물어보고, 어려우면 일을 다시 나눠요",
                              "evidence": [{"label": f"{'이슈' if mine else 'PR'} #{target}", "url": f"{gh_url}/{'issues' if mine else 'pull'}/{target}"}],
                              "options": [{"label": f"{m} 님께 안부 묻기", "recommended": True, "sub": f"#{target} 댓글", "actions": [
                                  Comment(number=target, body=f"@{m} 님, 요즘 어떠세요? 맡은 일 중에 막히거나 바쁜 게 있으면 편하게 말씀해 주세요. 다른 팀원과 나눌 수 있어요!")]}]})

    # 5. 맡기만 하고 손을 안 댄 할 일
    from .notice import is_submit_act
    quiet_people = {it["id"][6:] for it in items if it["id"].startswith("quiet-")}
    pr_text = " ".join(f"{p.title} {p.body} {p.branch}" for p in state.prs)
    stale = []
    for i in state.issues:
        up = parse_ts(i.updated_at or i.created_at)
        if (not i.assignees or not up or hours(up, now) < STALE_H or is_suspicious(i.body) or i.number in state.merged_refs
                or set(i.assignees) <= quiet_people or i.title.startswith("[승인 요청]") or is_submit_act(re.sub(r"^\[[^\]]*\]\s*", "", i.title))):
            continue
        if re.search(rf"(?<![\w/])#{i.number}\b|(?<!\d){i.number}(?!\d)[-_/]", pr_text):
            continue  # 이 할 일을 다루는 PR이 열려 있다
        targets = [a for a in i.assignees if a not in state.leads]
        if not targets:
            continue  # 팀장 자신의 할 일은 '내 할 일'에서 보고, 자기 자신에게 댓글을 보내지는 않는다
        stale.append((i, int(hours(up, now) // 24), targets))
    if stale:
        stale.sort(key=lambda x: -x[1])
        items.append({"id": "stale", "sev": "주의", "who": lead, "age": 0, "title": f"맡은 지 3일 넘게 그대로인 할 일 {len(stale)}개",
                      "why": " · ".join(f"#{i.number} {i.title} ({', '.join(targets)}, {d}일째)" for i, d, targets in stale[:3]),
                      "evidence": [issue_link(i, "진행 확인하러 가기") for i, _, _ in stale[:3]],
                      "options": [{"label": "담당자께 진행 상황 묻기", "recommended": True, "sub": f"이슈 {min(3, len(stale))}개에 댓글", "actions": [
                          Comment(number=i.number, body=f"{' '.join('@' + a for a in targets)} 님, 이 일은 어디까지 됐나요? 막힌 게 있으면 말씀해 주세요. 시작 전이면 PR이나 브랜치를 먼저 열어 두면 팀이 알 수 있어요!")
                          for i, _, targets in stale[:3]]}]})

    # 6. 초대를 수락하지 않은 팀원
    inv = [x for x in (sig.get("invites") or []) if (t := parse_ts(x.get("at"))) and hours(t, now) >= INVITE_H]
    if inv:
        items.append({"id": "invite", "sev": "주의", "who": lead, "age": 0,
                      "title": f"초대를 아직 수락하지 않은 팀원 {len(inv)}명 · {', '.join(x['login'] for x in inv[:3])}",
                      "why": "수락 전에는 할 일을 맡기거나 리뷰를 부탁할 수 없어요. 초대는 7일이 지나면 만료돼요"
                             + (" · 만료된 초대가 있어요" if any(x["expired"] for x in inv) else ""),
                      "evidence": [{"label": "초대 관리", "url": f"{gh_url}/settings/access"}],
                      "handoff": [{"label": "팀원에게 보낼 수락 링크", "url": f"{gh_url}/invitations"},
                                  {"label": "초대 다시 보내기 (관리자)", "url": f"{gh_url}/settings/access"}], "options": []})

    # 7. 수정 요청에 답이 없는 PR
    for p in state.prs:
        at = parse_ts(p.changes_at)
        if not at or p.draft or hours(at, now) < CHANGES_H:
            continue
        last = parse_ts((sig.get("changes") or {}).get(p.number))
        if last and last > at:
            continue
        items.append({"id": f"changes-{p.number}", "sev": "주의", "who": p.author, "age": hours(at, now) - CHANGES_H,
                      "title": f"PR #{p.number} 수정 요청에 {int(hours(at, now))}시간째 답이 없어요",
                      "why": f"{', '.join(p.changes_by)} 님이 고쳐 달라고 했는데 그 뒤로 새 커밋이 없어요",
                      "evidence": [{"label": f"PR #{p.number}", "url": p.url}],
                      "handoff": [{"label": "리뷰 내용 보기", "url": f"{p.url}/files"}, {"label": "브랜치 받기", "command": f"git fetch origin && git checkout {shlex.quote(p.branch)}"}],
                      "options": []})

    # 8. 리뷰 승인 없이 머지된 PR
    mu = sig.get("merged_unreviewed") or []
    if team and mu:
        from .triage import _load
        load = _load(state)
        due = deadline_of(state)
        # 제출 마감이 24시간 안이면 급해서 바로 머지하는 게 당연하다: 알림은 '정리'(맨 아래)로 낮추고 말투도 부드럽게
        near = due is not None and timedelta(0) <= due - now <= timedelta(hours=24)
        for m in mu[:3]:
            others = sorted((x for x in state.members if x != m["author"]), key=lambda x: load[x])
            if not others:
                continue
            load[others[0]] += 1  # 같은 사람에게 몰리지 않게: 방금 맡긴 일을 센 다음 PR은 다른 사람에게
            items.append({"id": f"selfmerge-{m['number']}", "sev": "정리" if near else "확인", "who": others[0], "who_why": "맡은 일이 가장 적은 팀원이라서 (같으면 아이디 순)", "age": 0,
                          "title": f"PR #{m['number']}이 리뷰 승인 없이 머지됐어요",
                          "why": f"{m['title']} · {m['author']} 님. " + ("마감이 가까워 바로 합친 것으로 보여요. 제출 전에 한 사람이 훑어보면 안전해요" if near else "급해서 그랬을 수 있어요. 머지된 뒤라도 한 사람이 훑어보면 실수를 일찍 찾아요"),
                          "evidence": [{"label": f"PR #{m['number']}", "url": m["url"]}],
                          "handoff": [{"label": f"PR #{m['number']} 머지 뒤 리뷰하기", "url": m["url"]}],
                          "options": []})

    # 9. 계정에 연결되지 않은 커밋 (기여도에 안 잡힌다). 이메일은 보여 주지 않는다
    loose: dict[str, list[dict]] = {}
    for c in sig.get("commits") or []:
        if not c["login"] and c["name"] and "[bot]" not in c["name"]:
            loose.setdefault(c["name"], []).append(c)
    if loose:
        names = sorted(loose, key=lambda n: -len(loose[n]))
        items.append({"id": "unlinked", "sev": "정리", "who": lead, "age": 0, "title": f"GitHub 계정에 안 잡힌 커밋 {sum(len(v) for v in loose.values())}개 · {', '.join(names[:3])}",
                      "why": "컴퓨터의 git 이메일이 GitHub 계정 이메일과 달라서 기여도에 안 보여요. 본인 컴퓨터에서 이메일을 맞추면 앞으로의 커밋부터 잡혀요",
                      "evidence": [{"label": f"{n}의 커밋", "url": loose[n][0]["url"]} for n in names[:3]],
                      "handoff": [{"label": "GitHub에 등록된 이메일 확인", "url": "https://github.com/settings/emails"},
                                  {"label": "git 이메일 맞추기", "command": 'git config --global user.email "GitHub에-등록한-이메일"'}], "options": []})

    # 10~11. 제출 마감 직전 · 마감 뒤 커밋
    due = deadline_of(state)
    if due:
        after = [c for c in sig.get("commits") or [] if (t := parse_ts(c["at"])) and t > due]
        after += [{"login": p["login"], "at": p["at"], "url": f"{gh_url}/activity", "sha": "", "name": p["login"]}
                  for p in sig.get("pushes") or [] if (t := parse_ts(p["at"])) and t > due and not any(c["at"] == p["at"] for c in after)]
        if after and now > due:
            before = next((c for c in sig.get("commits") or [] if (t := parse_ts(c["at"])) and t <= due), None)
            items.append({"id": "late-commit", "sev": "긴급", "who": lead, "age": 0,
                          "title": f"제출 마감 뒤에 올라온 커밋 {len(after)}개",
                          "why": f"{', '.join(sorted({c['login'] or c['name'] for c in after}))} 님. 대회에 따라 마감 뒤 수정은 실격 사유예요. 규정을 확인하고, 마감 전 마지막 커밋을 제출본으로 알려 두세요",
                          "evidence": [{"label": "마감 뒤 커밋", "url": after[0]["url"]}],
                          "handoff": ([{"label": "마감 전 마지막 커밋에 제출본 표시", "command": f"git tag submission {before['sha'][:7]} && git push origin submission"}] if before else []),
                          "options": []})
        elif timedelta(0) < due - now <= timedelta(hours=6):
            items.append({"id": "deadline-soon", "sev": "긴급", "who": lead, "age": 0, "title": f"제출 마감까지 {max(1, int((due - now).total_seconds() // 3600))}시간",
                          "why": "마감 뒤에는 푸시하지 마세요. 제출할 커밋이 정해지면 태그로 표시해 두면 심사 때 헷갈리지 않아요",
                          "evidence": [{"label": "제출 마감", "url": f"{gh_url}/milestones"}],
                          "handoff": [{"label": "지금 커밋을 제출본으로 표시", "command": "git tag submission && git push origin submission"}], "options": []})
    return items
