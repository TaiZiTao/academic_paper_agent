"""论文工作台滚动布局回归测试。"""

import re
from pathlib import Path


WORKSPACE_VIEW = Path(__file__).parents[1] / "web" / "src" / "views" / "PaperWorkspaceView.vue"
PAPER_REPORT = Path(__file__).parents[1] / "web" / "src" / "components" / "paper" / "PaperReport.vue"
FRONTEND_MAIN = Path(__file__).parents[1] / "web" / "src" / "main.ts"
VITE_CONFIG = Path(__file__).parents[1] / "web" / "vite.config.ts"


def _rule(source: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]+)\}}", source)
    assert match, f"missing CSS rule: {selector}"
    return match.group(1)


def test_reading_report_flex_children_can_shrink_into_scroll_container():
    source = WORKSPACE_VIEW.read_text(encoding="utf-8")

    assert re.search(r"min-height\s*:\s*0", _rule(source, ".reading-desk"))
    assert re.search(r"min-height\s*:\s*0", _rule(source, ".desk-content"))


def test_report_copy_does_not_claim_every_citation_is_verified():
    source = PAPER_REPORT.read_text(encoding="utf-8")

    assert "引用页码均经过校验" not in source
    assert "引用状态" in source


def test_frontend_registers_element_plus_components_on_demand():
    source = FRONTEND_MAIN.read_text(encoding="utf-8")

    assert 'import ElementPlus from "element-plus"' not in source
    assert "app.use(ElementPlus)" not in source
    assert "ElButton" in source


def test_vite_does_not_force_full_element_plus_into_one_chunk():
    source = VITE_CONFIG.read_text(encoding="utf-8")

    assert '"element-plus": ["element-plus"]' not in source
    assert 'presentation: ["pptxgenjs"]' in source
    assert 'scientific: ["katex", "marked"]' in source
