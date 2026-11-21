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


def open_late_card(page, app_url):
    mock_late_progress(page)
    open_app(page, app_url)
    expect(page.get_by_text("전체 진도가 늦어요: 시간 44% 지났는데 제출물 0% 끝남")).to_be_visible(timeout=10_000)
    page.get_by_role("button", name="자세히").click()
    expect(page.get_by_text("늦어지고 있어요. 어떻게 할까요?")).to_be_visible()


def test_tabs_calendar_and_mypage_keep_working(page, app_url):
    open_app(page, app_url)

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
    page.get_by_role("button", name="다음", exact=True).click()
    page.get_by_role("button", name="저장소 연결하기").click()
    expect(page.get_by_role("button").filter(has_text="team-matzip/knu-matzip")).to_be_visible(timeout=10_000)


def test_late_progress_opens_today_tasks(page, app_url):
    open_late_card(page, app_url)
    page.get_by_role("button", name="오늘 할 일 보기").click()
    expect(page.get_by_text(re.compile(r"처리할 일 \d+개를 찾았어요"))).to_be_visible(timeout=10_000)


def test_late_progress_opens_replanning(page, app_url):
    open_late_card(page, app_url)
    page.get_by_role("button", name="계획 다시 짜기").click()
    expect(page.get_by_text("제출 마감 날짜를 공고에서 찾지 못했어요.", exact=False)).to_be_visible(timeout=10_000)
    expect(page.get_by_role("button", name="이 날짜로 계획 짜기")).to_be_visible()
