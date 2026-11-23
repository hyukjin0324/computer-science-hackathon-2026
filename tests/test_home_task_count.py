from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "public" / "index.html").read_text(
    encoding="utf8"
)


def test_home_summary_and_task_list_share_the_same_work_collection():
    assert "function myHomeWork(me2, items)" in HTML
    assert HTML.count("myHomeWork(me2, items)") >= 3
    assert "mineWork.items.length + mineWork.tasks.length" in HTML
    assert "const ts = mineWork.tasks" in HTML


def test_progress_findings_are_not_double_counted_as_my_tasks():
    assert "me2 ? mineWork.items.length + mineWork.tasks.length" in HTML
    assert "mineWork.tasks.length + finds.length" not in HTML
