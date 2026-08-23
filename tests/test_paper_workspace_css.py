"""论文工作台滚动布局回归测试。"""

import re
from pathlib import Path


WORKSPACE_VIEW = Path(__file__).parents[1] / "web" / "src" / "views" / "PaperWorkspaceView.vue"


def _rule(source: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]+)\}}", source)
    assert match, f"missing CSS rule: {selector}"
    return match.group(1)


def test_reading_report_flex_children_can_shrink_into_scroll_container():
    source = WORKSPACE_VIEW.read_text(encoding="utf-8")

    assert re.search(r"min-height\s*:\s*0", _rule(source, ".reading-desk"))
    assert re.search(r"min-height\s*:\s*0", _rule(source, ".desk-content"))
