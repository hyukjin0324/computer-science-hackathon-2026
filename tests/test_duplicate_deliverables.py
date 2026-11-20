"""공고에 같은 제출물이 두 번 적혀 있어도 일정 만들기(마일스톤·이슈)가 실패하지 않는지."""

import pytest
from fastapi.testclient import TestClient

from app import main
from app import notice as nm
from app.notice import Deliverable, NoticeExtract

client = TestClient(main.app)
NOTICE = ("2026 AI 해커톤\n제출 마감: 2026년 12월 10일 18:00\n"
          "제출물: 발표 자료, 시연 영상\n※ 발표 자료, 시연 영상을 모두 제출해 주세요.")


@pytest.fixture(autouse=True)
def _fresh():
    main._demo.reset()
    main._EXECUTED.clear()
    nm._CACHE.clear()
    yield


def test_repeated_deliverables_are_read_once():
    r = client.post("/api/notice", json={"text": NOTICE}).json()
    assert [d["name"] for d in r["extract"]["deliverables"]] == ["발표 자료", "시연 영상"]  # 전에는 4개


def test_plan_with_repeated_deliverables_runs():
    r = client.post("/api/notice", json={"text": NOTICE}).json()
    labels = [s["label"] for s in r["plan"]["steps"]]
    assert labels.count("할 일 추가: [제출물] 발표 자료") == 1
    run = client.post("/api/execute", json={"token": r["plan"]["token"]}).json()
    assert run["ok"], run  # 전에는 '같은 제목의 열린 이슈가 이미 있어요'로 제안 전체가 실행되지 않았다
    titles = [i["title"] for i in main._demo.d["issues"] if i["title"].startswith("[제출물]")]
    assert sorted(titles) == ["[제출물] 발표 자료", "[제출물] 시연 영상"]


def test_plan_dedupes_even_old_saved_analysis():
    """저장소에 저장된 예전 분석(중복 포함)을 쓰더라도 이슈는 한 번만."""
    from app.state import build_state
    ex = NoticeExtract(title="t", facts=[], submit_deadline_iso="2026-12-10T18:00:00+09:00",
                       deliverables=[Deliverable(name="발표 자료", quote="발표 자료"), Deliverable(name="발표  자료", quote="발표 자료"),
                                     Deliverable(name="시연 영상", quote="시연 영상")])
    actions, _ = nm.plan(ex, build_state(main._demo))
    titles = [a.title for a in actions if a.type == "create_issue"]
    assert titles == ["[제출물] 발표 자료", "[제출물] 시연 영상"]
