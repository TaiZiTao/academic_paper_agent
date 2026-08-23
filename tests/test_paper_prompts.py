"""论文精读提示词的输入预算测试。"""

from app.paper.prompts import build_report_prompt, build_translation_prompt
from app.paper.schemas import PaperChunkData


def test_report_prompt_limits_large_paper_evidence_while_covering_every_page():
    chunks = []
    ordinal = 0
    for page in range(1, 16):
        for chunk_index in range(5):
            marker = f"page-{page}-chunk-{chunk_index}"
            chunks.append(
                PaperChunkData(
                    paper_id=1,
                    chunk_id=f"paper-1-chunk-{ordinal}",
                    section="Methods",
                    page_start=page,
                    page_end=page,
                    ordinal=ordinal,
                    content=marker + " " + ("evidence " * 145),
                )
            )
            ordinal += 1

    prompt = build_report_prompt(
        paper_title="Large Paper",
        metadata={"title": "Large Paper"},
        sections=[],
        chunks=chunks,
    )

    assert len(prompt) <= 45_000
    for page in range(1, 16):
        assert f"page={page};" in prompt


def test_translation_prompt_tells_model_to_skip_visual_content():
    prompt = build_translation_prompt(
        paper_title="PromptSR",
        section="III. METHOD",
        page_start=3,
        page_end=4,
        source_text="Body paragraph.",
    )

    assert "不要翻译图片、表格、图注、表注及其内部文字" in prompt
    assert "保留正文和公式" in prompt
    assert "LaTeX" in prompt
    assert "<sub>" in prompt
    assert "<sup>" in prompt


def test_library_qa_prompt_includes_history():
    from app.paper.prompts import build_library_qa_prompt
    import types
    p = types.SimpleNamespace(id=1, title="T1", research_field="超分辨率", publication_year=2024)
    c = types.SimpleNamespace(paper_id=1, section="s", page_start=1, content="evidence")
    prompt = build_library_qa_prompt("那 PromptSR 呢", [p], [c], [{"role": "user", "content": "超分有什么问题"}, {"role": "assistant", "content": "回答"}])
    assert "超分有什么问题" in prompt  # 历史包含在 prompt 中

def test_library_catalog_prompt_includes_history():
    from app.paper.prompts import build_library_catalog_prompt
    import types
    p = types.SimpleNamespace(id=1, title="T1", research_field="超分辨率", publication_year=2024)
    prompt = build_library_catalog_prompt("还有哪些", [p], [{"role": "user", "content": "有什么论文"}])
    assert "有什么论文" in prompt


def test_relevance_prompt_output_domain():
    from app.paper.prompts import build_relevance_prompt
    prompt = build_relevance_prompt("对比 ESRGAN 和 SRGAN 哪篇更好")
    assert "true" in prompt  # 输出域明确: true
    assert "false" in prompt  # 输出域明确: false


def test_general_chat_prompt_structure():
    from app.paper.prompts import build_general_chat_prompt
    prompt = build_general_chat_prompt("q", [{"role": "user", "content": "h"}])
    assert '"content"' in prompt  # JSON 结构字段
    assert '"citations"' in prompt  # JSON 结构字段
    assert '"h"' in prompt  # 历史内容包含在 prompt 中
