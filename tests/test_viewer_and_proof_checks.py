"""막혔을 때 오류에 빈 목록을 보내지 않는지, 나에 대한 '활동 없음' 카드가 나에게 안 보이는지, '제출 완료 기록'은 제출 확인 이슈만 받는지."""

from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def _as(login):
    return {"X-Bento-User": login}


def test_self_contact_block_sends_no_empty_error_list():
    main._demo.reset()
    item = next(i for i in client.get("/api/triage").json()["items"] if i["id"] == "review-14")
    opt = next(o for o in item["options"] if "doyoon" in o["label"])  # 아무도 고르지 않았을 때 받은 'doyoon 님께 리뷰 부탁하기'
    r = client.post("/api/execute", json={"token": opt["proposal"]["token"]}, headers=_as("doyoon"))
    assert r.status_code == 409
    assert "errors" not in r.json()["detail"]  # 빈 목록이 있으면 화면이 문장 끝에 '·'만 붙인다


def test_quiet_card_is_hidden_from_that_person_only():
    main._demo.reset()
    ids = lambda who: {i["id"] for i in client.get("/api/triage", headers=_as(who)).json()["items"]}
    assert "quiet-doyoon" not in ids("doyoon")
    assert "quiet-doyoon" in ids("jiwoo")  # 팀장에게는 그대로


def test_proof_only_closes_the_submit_confirm_issue():
    main._demo.reset()
    r = client.post("/api/submit/proof", json={"issue": 12, "proof": "접수번호 HK-0042"})  # 제출 확인과 관계없는 이슈
    assert r.status_code == 400
