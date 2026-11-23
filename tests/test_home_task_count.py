from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "public" / "index.html").read_text(
    encoding="utf8"
)


def test_home_task_list_uses_assigned_github_work():
    assert "function myHomeWork(me2, items)" in HTML
    assert "const ts = mineWork.tasks" in HTML


def test_home_attention_count_is_separate_from_all_assigned_tasks():
    assert "items.filter((i) => i.owner === me2 || i.level >= 2).length" in HTML
    assert "(me2 ? mineN : items.length)) + finds.length" in HTML
    assert '`${me2 ? "내가 " : "오늘 "}챙길 일 ${todo}개`' in HTML
    assert "mineWork.items.length + mineWork.tasks.length" not in HTML
