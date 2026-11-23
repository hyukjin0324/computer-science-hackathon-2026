"""발표와 시연에서 끊기면 안 되는 화면 흐름."""

import json
import re
import time

import pytest

expect = pytest.importorskip("playwright.sync_api").expect
pytestmark = pytest.mark.e2e


LATE_PROGRESS = {
    "findings": [{
        "id": "pace",
        "sev": "주의",
        "title": "전체 진도가 늦어요: 시간 44% 지났는데 제출물 0% 끝남",
        "why": "'해커톤 제출' 열린 이슈 2개",
    }],
    "moves": [],
    "assign": [],
    "scope": None,
    "summary": "팀플 점검: 챙길 일이 있어요",
}

LIFECYCLE_EXTRACT = {
    "title": "2026 컴퓨터학부 해커톤",
    "facts": [{
        "label": "참가 자격",
        "value": "컴퓨터학부 재학생, 2~4인 팀",
        "quote": "컴퓨터학부 재학생, 2~4인 팀",
    }],
    "team_min": 2,
    "team_max": 4,
    "apply_fields": ["팀명", "팀원", "아이디어 한 줄"],
    "apply_deadline": "11월 18일(수) 23:59",
    "apply_deadline_iso": "2026-11-18T23:59:00+09:00",
    "submit_deadline": "11월 24일(화) 12:00",
    "submit_deadline_iso": "2026-11-24T12:00:00+09:00",
    "deliverables": [
        {"name": "GitHub 저장소 링크(공개)", "quote": "GitHub 저장소 링크(공개)"},
        {"name": "README에 실행 방법과 배포 링크", "quote": "README에 실행 방법과 배포 링크"},
        {"name": "발표 자료 PDF", "quote": "발표 자료 PDF"},
        {"name": "3분 이내 시연 영상", "quote": "3분 이내 시연 영상"},
    ],
}

LIFECYCLE_NOTICE = """[체험용 예시 공고] 2026 컴퓨터학부 해커톤
주제: 컴퓨터학부 학생에게 필요한 서비스
참가 자격: 컴퓨터학부 재학생, 2~4인 팀
신청: 11/18(수) 23:59까지 신청 폼 제출 (팀명, 팀원, 아이디어 한 줄)
개발 기간: 11/11(수) 14:00 ~ 11/24(화) 12:00
제출물: ① GitHub 저장소 링크(공개) ② README에 실행 방법과 배포 링크 ③ 발표 자료 PDF ④ 3분 이내 시연 영상
제출: 11/24(화) 12:00까지 제출 폼으로 제출
심사 기준: 문제 정의 30%, 완성도 30%, 창의성 20%, 발표 20%"""


def open_app(page, app_url):
    page.goto(app_url, wait_until="domcontentloaded")
    start = page.get_by_role("button", name="시작하기")
    expect(start).to_be_visible(timeout=10_000)
    start.click()
    expect(page.get_by_role("button", name="홈", exact=True)).to_be_visible()


def mock_late_progress(page):
    page.route(
        "**/api/progress",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(LATE_PROGRESS, ensure_ascii=False)),
    )
    extract = {"title": "테스트 해커톤", "submit_deadline_iso": None, "deliverables": []}
    page.add_init_script(
        "localStorage.setItem('bento:extract:r:team-matzip/knu-matzip', JSON.stringify("
        + json.dumps(extract, ensure_ascii=False)
        + "));"
    )


def seed_lifecycle_draft(page):
    draft = {
        "id": "e2e-lifecycle",
        "title": LIFECYCLE_EXTRACT["title"],
        "text": LIFECYCLE_NOTICE,
        "noAi": True,
        "extract": LIFECYCLE_EXTRACT,
    }
    page.add_init_script(
        "const draft = " + json.dumps(draft, ensure_ascii=False) + ";"
        "localStorage.setItem('bento:drafts', JSON.stringify([draft]));"
        "localStorage.setItem('bento:cur', JSON.stringify({draft: draft.id}));"
    )


def mock_submission_checks(page):
    requests = []
    incomplete = [
        {"name": "저장소 공개", "status": "ok", "detail": "누구나 볼 수 있어요", "evidence": []},
        {"name": "README 실행 방법", "status": "ok", "detail": "실행 방법이 적혀 있어요", "evidence": []},
        {"name": "발표 자료 링크 열림", "status": "fail", "detail": "링크가 아직 없어요", "evidence": []},
        {"name": "시연 영상 링크 열림", "status": "fail", "detail": "링크가 아직 없어요", "evidence": []},
    ]
    complete = [{**row, "status": "ok", "detail": "열려요"} for row in incomplete]
    human = [
        {"id": "slides", "text": "발표 자료가 최신 버전이고 지금 기능과 맞아요"},
        {"id": "video", "text": "시연 영상이 최신 기능을 보여 주고, 공개 범위가 '링크가 있는 사용자'예요"},
        {"id": "agree", "text": "팀원 모두 이 버전으로 제출하는 데 동의했어요"},
        {"id": "form", "text": "제출 폼의 팀명·대표자 정보가 맞아요"},
    ]

    def handle(route):
        requests.append(route.request.post_data_json)
        rows = incomplete if len(requests) == 1 else complete
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "rows": rows,
                "head": {"sha": "c0ffee1234567", "short": "c0ffee1", "url": "https://example.test/commit", "branch": "main"},
                "lock": None,
                "human": human,
                "ready": len(requests) > 1,
                "deploy": "https://example.test",
                "confirm": None,
                "members": ["jiwoo", "doyoon"],
            }, ensure_ascii=False),
        )

    page.route("**/api/submit-check", handle)
    return requests


def open_late_card(page, app_url):
    mock_late_progress(page)
    open_app(page, app_url)
    expect(page.get_by_text("전체 진도가 늦어요: 시간 44% 지났는데 제출물 0% 끝남")).to_be_visible(timeout=10_000)
    page.get_by_role("button", name="자세히").click()
    expect(page.get_by_text("늦어지고 있어요. 어떻게 할까요?")).to_be_visible()


def test_tabs_calendar_and_mypage_keep_working(page, app_url):
    open_app(page, app_url)

    expect(page.get_by_text("데모 데이터", exact=True)).to_be_visible()

    page.get_by_role("button", name="캘린더", exact=True).click()
    expect(page.get_by_role("heading", name="캘린더")).to_be_visible()
    expect(
        page.get_by_text("아직 일정이 없어요").or_(page.get_by_role("button", name="이전 달"))
    ).to_be_visible(timeout=10_000)

    page.get_by_role("button", name="마이페이지", exact=True).click()
    expect(page.get_by_role("heading", name="마이페이지")).to_be_visible()
    for section in ("계정", "팀플", "앱 사용", "도움"):
        expect(page.get_by_text(section, exact=True)).to_be_visible()

    page.get_by_role("button", name=re.compile("팀플 목록")).click()
    expect(page.get_by_role("heading", name="팀플 목록")).to_be_visible()
    page.get_by_role("button", name=re.compile("knu-matzip")).click()
    expect(page).to_have_url(re.compile(r"#me-projects$"))


def test_repository_shared_notice_overrides_stale_browser_notice(page, app_url):
    stale = {"title": "다른 브라우저에 남은 대회", "deliverables": []}
    shared = {"title": "팀 저장소에 연결된 공용 대회", "deliverables": []}
    page.add_init_script(
        "localStorage.setItem('bento:repo', 'team-matzip/knu-matzip');"
        "localStorage.setItem('bento:cur', JSON.stringify({repo: 'team-matzip/knu-matzip'}));"
        "localStorage.setItem('bento:extract:r:team-matzip/knu-matzip', JSON.stringify("
        + json.dumps(stale, ensure_ascii=False)
        + "));"
    )
    page.route(
        "**/api/notice/saved?repo=*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"extract": shared}, ensure_ascii=False),
        ),
    )

    open_app(page, app_url)

    project_button = page.get_by_role("button", name="팀플 바꾸기")
    expect(project_button).to_contain_text("팀 저장소에 연결된 공용 대회", timeout=10_000)
    expect(project_button).not_to_contain_text("다른 브라우저에 남은 대회")


def test_home_does_not_recommend_another_contest_after_one_is_selected(page, app_url):
    selected = {"title": "이미 정한 팀 대회", "submit_deadline_iso": "2026-11-30T12:00:00+09:00", "deliverables": []}
    recommendation = {
        "id": "another-contest",
        "title": "새로 추천된 다른 대회",
        "deadline_iso": "2026-11-27T23:59:00+09:00",
        "deadline_text": "11월 27일 23:59",
        "tags": ["AI"],
        "source_name": "테스트",
        "ai_ok": False,
        "url": "https://example.test/another",
    }
    page.route(
        "**/api/notice/saved?repo=*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"extract": selected}, ensure_ascii=False),
        ),
    )
    page.route(
        "**/api/discover?*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "items": [recommendation],
                "pending": [],
                "sources": [],
                "not_auto": [],
                "checked_at": "2026-11-24T02:00:00+09:00",
            }, ensure_ascii=False),
        ),
    )

    open_app(page, app_url)

    expect(page.get_by_text("다음 마감 · 이미 정한 팀 대회", exact=True)).to_be_visible(timeout=10_000)
    expect(page.get_by_text("새로 추천된 다른 대회", exact=True)).to_have_count(0)


def test_missing_github_installation_offers_recovery_actions(page, app_url):
    open_app(page, app_url)

    page.evaluate("""() => {
        showTab("chat");
        const error = new Error("team/missing에 Bento 앱이 설치되지 않았거나 저장소가 없어요");
        error.status = 404;
        fail(error);
    }""")
    expect(page.get_by_text("team/missing에 Bento 앱이 설치되지 않았거나 저장소가 없어요", exact=True).last).to_be_visible()
    expect(page.get_by_role("button", name="다른 팀플 고르기", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="다시 확인", exact=True)).to_be_visible()


def test_role_picker_edits_only_my_roles_without_limiting_count(page, app_url):
    saved = []

    def roles(route):
        if route.request.method == "POST":
            saved.append(route.request.post_data_json)
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"saved": True, "who": "seoyeon"}))
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "members": ["jiwoo", "seoyeon"],
                "roles": {},
                "choices": {"lead": "팀장", "pm": "기획·PM", "fe": "프론트엔드", "be": "백엔드", "ds": "디자인", "pt": "발표"},
            }, ensure_ascii=False),
        )

    page.route("**/api/roles**", roles)
    open_app(page, app_url)
    page.evaluate("""() => { setMe("seoyeon"); showTab("me"); openMe("roles"); }""")

    seoyeon = page.get_by_role("group", name="내 역할")
    expect(seoyeon).to_be_visible()
    jiwoo = page.get_by_role("group", name="jiwoo 역할")
    expect(jiwoo).to_be_visible()
    jiwoo.get_by_role("button", name="기획·PM", exact=True).click()
    expect(page.get_by_text("본인 역할만 수정할 수 있어요", exact=True)).to_be_visible()
    seoyeon.get_by_role("button", name="팀장", exact=True).click()
    expect(seoyeon.locator('[data-role="lead"]')).to_have_attribute("aria-pressed", "true")

    for code, name in (("pm", "기획·PM"), ("fe", "프론트엔드"), ("be", "백엔드"), ("ds", "디자인"), ("pt", "발표")):
        seoyeon.get_by_role("button", name=name, exact=True).click()
        expect(seoyeon.locator(f'[data-role="{code}"]')).to_have_attribute("aria-pressed", "true")

    page.get_by_role("button", name="내 역할 저장", exact=True).click()
    expect(page.get_by_text("내 역할을 저장했어요", exact=True)).to_be_visible()
    assert len(saved) == 1
    assert saved[0]["roles"]["seoyeon"] == ["lead", "pm", "fe", "be", "ds", "pt"]
    assert set(saved[0]["roles"]) == {"seoyeon"}


def test_existing_repository_path_reaches_repository_picker(page, app_url):
    draft = {
        "id": "e2e-repo",
        "title": "테스트 해커톤",
        "text": "테스트 해커톤 공고",
        "noAi": True,
        "applied": int(time.time() * 1000),
        "extract": {"title": "테스트 해커톤", "no_apply": True, "deliverables": []},
    }
    encoded = json.dumps(draft, ensure_ascii=False)
    page.add_init_script(
        "const draft = " + encoded + ";"
        "localStorage.setItem('bento:drafts', JSON.stringify([draft]));"
        "localStorage.setItem('bento:cur', JSON.stringify({draft: draft.id}));"
    )
    open_app(page, app_url)

    page.get_by_role("button", name="저장소 연결").click()
    expect(page.get_by_role("button", name="이미 저장소가 있어요")).to_be_visible()
    page.get_by_role("button", name="이미 저장소가 있어요").click()
    expect(page.get_by_text("데모에서는 설치 없이 예시 저장소로 연결해 볼 수 있어요.")).to_be_visible()
    page.get_by_role("button", name="저장소 연결하기").click()
    picker = page.get_by_role("button").filter(has_text="team-matzip/knu-matzip")
    expect(picker).to_be_visible(timeout=10_000)
    picker.click()
    step = page.locator('[aria-current="step"]').filter(has_text="팀원 초대")
    expect(step).to_be_visible()
    expect(step.get_by_role("button", name="초대했어요")).to_be_disabled()
    expect(step.get_by_label("GitHub 아이디")).to_be_hidden()
    step.get_by_role("button", name="GitHub 팀원 초대").click()
    step.get_by_label("GitHub 아이디").fill("e2e-invitee")
    step.get_by_role("button", name="팀원 추가", exact=True).click()
    step.get_by_label("GitHub 아이디").nth(1).fill("e2e-teammate")
    step.get_by_role("button", name="초대 내용 확인").click()
    approval = page.get_by_role("dialog", name="team-matzip/knu-matzip에 팀원 초대")
    expect(approval.get_by_text("@e2e-invitee님을 팀 저장소에 초대")).to_be_visible()
    expect(approval.get_by_text("@e2e-teammate님을 팀 저장소에 초대")).to_be_visible()
    approval.get_by_role("button", name="승인하고 초대 보내기").click()
    expect(approval.get_by_text("@e2e-invitee님의 데모 초대를 만들었어요.")).to_be_visible()
    expect(approval.get_by_text("@e2e-teammate님의 데모 초대를 만들었어요.")).to_be_visible()
    approval.get_by_role("button", name="확인", exact=True).click()
    expect(step.get_by_role("button", name="초대했어요")).to_be_enabled()


def test_application_collaboration_and_submission_lifecycle(page, app_url):
    seed_lifecycle_draft(page)
    submit_requests = mock_submission_checks(page)
    open_app(page, app_url)

    # 신청 준비 → 자격 확인 → 사람이 직접 신청했다는 완료 기록
    page.get_by_role("button", name="신청 준비", exact=True).click()
    expect(page.get_by_text("1단계 · 참가 자격 확인")).to_be_visible(timeout=10_000)
    page.get_by_role("button", name="확인했어요", exact=True).click()
    expect(page.get_by_role("button", name="신청했어요", exact=True)).to_be_visible()
    page.get_by_role("button", name="신청했어요", exact=True).click()
    expect(page.get_by_text("신청 완료로 기록했어요. 이제 팀 저장소를 준비해요.")).to_be_visible()

    # 기존 저장소와 연결하면 신청 기록이 유지되고 협업 단계로 넘어간다.
    page.get_by_role("button", name="이미 저장소가 있어요", exact=True).click()
    page.get_by_role("button", name="저장소 연결하기", exact=True).click()
    picker = page.get_by_role("button").filter(has_text="team-matzip/knu-matzip")
    expect(picker).to_be_visible(timeout=10_000)
    picker.click()
    journey = page.get_by_role("group", name="준비 순서")
    expect(journey.locator(".path > li.done")).to_have_count(3)
    expect(journey.locator('[aria-current="step"]')).to_contain_text("4팀원 초대")
    expect(journey.locator(".path > li").last).to_have_text("5연결 완료")
    page.get_by_role("button", name="나중에 할게요", exact=True).click()
    connected = page.get_by_text("team-matzip/knu-matzip에 연결했어요. 신청을 마쳤으니 이제 협업 단계예요.")
    expect(connected.last).to_be_visible(timeout=10_000)
    page.get_by_role("button", name="나중에", exact=True).click()

    page.get_by_role("button", name="홈", exact=True).click()
    expect(page.locator('[aria-label^="지금 단계: 협업"]')).to_be_visible()
    expect(page.get_by_role("heading", name="팀원 별 할 일")).to_be_visible()

    # 공고에서 읽은 필수 제출물과 현재 제출물을 대조한다.
    page.get_by_role("button", name=re.compile(r"^대화(?: \d+)?$")).click()
    page.get_by_role("button", name="제출 전 점검하기", exact=True).click()
    info_card = page.locator(".task").filter(has_text="1단계 · 제출 정보")
    expect(info_card).to_be_visible()
    info_card.get_by_role("button", name="제출 전 점검하기", exact=True).click()
    expect(page.get_by_text("고칠 것 · 2개", exact=True)).to_be_visible(timeout=10_000)
    expect(page.locator(".crow").filter(has_text="발표 자료 링크 열림")).to_be_visible()
    expect(page.locator(".crow").filter(has_text="시연 영상 링크 열림")).to_be_visible()

    assert [item["name"] for item in submit_requests[0]["deliverables"]] == [
        "GitHub 저장소 링크(공개)",
        "README에 실행 방법과 배포 링크",
        "발표 자료 PDF",
        "3분 이내 시연 영상",
    ]

    # 보완 뒤 다시 점검하면 사람 확인 → 직접 제출 → 완료 기록까지 열린다.
    page.get_by_role("button", name="다시 점검하기", exact=True).click()
    expect(page.get_by_text("Bento가 확인함 · 4개 통과 (근거 보기)")).to_be_visible(timeout=10_000)
    visible_checks = page.locator(".chat-check input[type=checkbox]:visible")
    expect(visible_checks).to_have_count(4)
    for checkbox in visible_checks.all():
        checkbox.check()
    page.get_by_role("button", name="확인 완료", exact=True).click()
    page.get_by_role("button", name="확인 없이 진행", exact=True).click()
    page.get_by_role("button", name="완료 기록하기", exact=True).click()
    page.get_by_role("button", name="제출 완료로 기록", exact=True).click()

    expect(page.get_by_text("제출 완료로 기록했어요. 팀플 목록의 숨긴 팀플로 옮겼어요.")).to_be_visible()
    page.get_by_role("button", name="홈", exact=True).click()
    expect(page.locator('[aria-label^="지금 단계: 끝"]')).to_be_visible()

    page.get_by_role("button", name="팀플 바꾸기").click()
    sheet = page.get_by_role("dialog", name="팀플 바꾸기")
    expect(sheet.get_by_text("숨긴 팀플 1개", exact=True)).to_be_visible()
    hidden_repo = sheet.get_by_role("button").filter(has_text="knu-matzip")
    expect(hidden_repo).to_contain_text("다시 표시")
    hidden_repo.click()
    expect(page.get_by_text("팀플을 다시 표시했어요")).to_be_visible()

    page.get_by_role("button", name="팀플 바꾸기").click()
    sheet = page.get_by_role("dialog", name="팀플 바꾸기")
    expect(sheet.get_by_text("숨긴 팀플 1개", exact=True)).to_have_count(0)
    assert len(submit_requests) == 2


def test_closed_issues_or_past_deadline_do_not_hide_project(page, app_url):
    open_app(page, app_url)
    project = {"repo": "team/finished-tasks", "error": False, "due": "2020-01-01", "total": 3, "done": 3}

    assert page.evaluate("project => isProjectHidden(project)", project) is False
    page.evaluate("setProjectHidden('r:team/finished-tasks', true)")
    assert page.evaluate("project => isProjectHidden(project)", project) is True
    page.evaluate("setProjectHidden('r:team/finished-tasks', false)")
    assert page.evaluate("project => isProjectHidden(project)", project) is False


def test_late_progress_opens_today_tasks(page, app_url):
    open_late_card(page, app_url)
    page.get_by_role("button", name="오늘 할 일 보기").click()
    expect(page.get_by_text(re.compile(r"처리할 일 \d+개를 찾았어요"))).to_be_visible(timeout=10_000)


def test_late_progress_opens_replanning(page, app_url):
    open_late_card(page, app_url)
    page.get_by_role("button", name="계획 다시 짜기").click()
    expect(page.get_by_text("제출 마감 날짜를 공고에서 찾지 못했어요.", exact=False)).to_be_visible(timeout=10_000)
    # 말풍선 뒤에 날짜 고르는 창이 바로 올라오고, 날짜를 골라 '확인'하면 '이 날짜로 계획 짜기'가 나온다
    sheet = page.get_by_role("dialog", name="제출 마감")
    expect(sheet).to_be_visible()
    expect(page.get_by_role("button", name="이 날짜로 계획 짜기")).to_be_hidden()
    sheet.locator(".dp-day:not(.past):not([disabled])").first.click()
    sheet.get_by_role("button", name="확인").click()
    expect(sheet).to_be_hidden()
    expect(page.get_by_role("button", name="이 날짜로 계획 짜기")).to_be_visible()
