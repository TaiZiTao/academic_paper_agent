"""
Parser 模块测试

覆盖：
- txt / md 文件读取
- 不支持的文件类型报错
- 文本清洗
- chunk 切分（含 overlap 验证）
- parse_file 集成流程
"""

import pytest

from app.parser import (
    Document,
    DocumentChunk,
    chunk_document,
    chunk_text,
    clean_text,
    load_file,
    load_markdown,
    load_text,
    parse_file,
)
from app.parser.loader import load_file as _load_file


# ============================================================
# Test Fixtures
# ============================================================

@pytest.fixture
def txt_file(tmp_path):
    """创建一个临时 .txt 文件"""
    file = tmp_path / "test.txt"
    file.write_text("Hello World\nThis is a test file.\n", encoding="utf-8")
    return str(file)


@pytest.fixture
def md_file(tmp_path):
    """创建一个临时 .md 文件"""
    file = tmp_path / "test.md"
    file.write_text("# Title\n\nSome **markdown** content.\n", encoding="utf-8")
    return str(file)


@pytest.fixture
def long_text():
    """生成长文本（~1500 字符），用于 chunk 测试"""
    paragraph = "This is paragraph number {i}. It contains some repeated content " \
                "to make it longer so we can test the chunking functionality properly. "
    return " ".join(paragraph.format(i=i) for i in range(20))


@pytest.fixture
def sample_document():
    """创建一个示例 Document"""
    return Document(
        filename="sample.txt",
        content="这是测试内容。" * 100,
        metadata={"source": "test"},
    )


# ============================================================
# Loader Tests
# ============================================================

class TestLoadText:
    """纯文本文件读取测试"""

    def test_load_text_returns_string(self, txt_file):
        result = load_text(txt_file)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_load_text_content_correct(self, txt_file):
        result = load_text(txt_file)
        assert "Hello World" in result

    def test_load_text_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_text("/nonexistent/file.txt")


class TestLoadMarkdown:
    """Markdown 文件读取测试"""

    def test_load_markdown_returns_string(self, md_file):
        result = load_markdown(md_file)
        assert isinstance(result, str)

    def test_load_markdown_content_correct(self, md_file):
        result = load_markdown(md_file)
        assert "# Title" in result
        assert "**markdown**" in result


class TestLoadFile:
    """文件分发器测试"""

    @pytest.mark.asyncio
    async def test_load_file_txt_returns_document(self, txt_file):
        doc = await load_file(txt_file)
        assert isinstance(doc, Document)
        assert doc.filename == "test.txt"

    @pytest.mark.asyncio
    async def test_load_file_md_returns_document(self, md_file):
        doc = await load_file(md_file)
        assert isinstance(doc, Document)
        assert doc.filename == "test.md"

    @pytest.mark.asyncio
    async def test_load_file_metadata(self, txt_file):
        doc = await load_file(txt_file)
        assert doc.metadata["file_type"] == ".txt"
        assert "file_size" in doc.metadata

    @pytest.mark.asyncio
    async def test_load_file_unsupported_type(self, tmp_path):
        file = tmp_path / "test.docx"
        file.write_text("dummy")
        with pytest.raises(ValueError, match="Unsupported file type"):
            await load_file(str(file))


# ============================================================
# Cleaner Tests
# ============================================================

class TestCleanText:
    """文本清洗测试"""

    def test_clean_text_normalizes_line_endings(self):
        text = "line1\r\nline2\rline3\n"
        result = clean_text(text)
        assert "\r\n" not in result
        assert "\r" not in result

    def test_clean_text_removes_control_chars(self):
        text = "Hello\x00\x01World"
        result = clean_text(text)
        assert "\x00" not in result
        assert "\x01" not in result

    def test_clean_text_preserves_newlines_and_tabs(self):
        text = "col1\tcol2\nrow2"
        result = clean_text(text)
        assert "\t" in result
        assert "\n" in result

    def test_clean_text_collapses_excessive_blank_lines(self):
        text = "line1\n\n\n\n\nline2"
        result = clean_text(text)
        # 3+ blank lines collapsed to 2
        assert "\n\n\n\n" not in result

    def test_clean_text_strips_whitespace(self):
        text = "  \n  hello  \n  "
        result = clean_text(text)
        assert result.startswith("hello")

    def test_clean_text_empty_string(self):
        result = clean_text("")
        assert result == ""


# ============================================================
# Chunker Tests
# ============================================================

class TestChunkText:
    """文本切分测试"""

    def test_chunk_text_single_chunk(self):
        """短文本 → 单个 chunk"""
        text = "Hello World"
        chunks = chunk_text(text, chunk_size=100, overlap=10)
        assert len(chunks) == 1
        assert chunks[0].content == "Hello World"

    def test_chunk_text_multiple_chunks(self, long_text):
        """长文本 → 多个 chunk"""
        chunks = chunk_text(long_text, chunk_size=500, overlap=50)
        assert len(chunks) > 1

    def test_chunk_text_chunk_size_respected(self, long_text):
        """每个 chunk 不超过 chunk_size"""
        chunks = chunk_text(long_text, chunk_size=500, overlap=50)
        for chunk in chunks:
            assert len(chunk.content) <= 500

    def test_chunk_text_overlap_works(self, long_text):
        """验证 overlap 生效：相邻 chunk 共享内容"""
        chunk_size = 500
        overlap = 50
        chunks = chunk_text(long_text, chunk_size=chunk_size, overlap=overlap)

        if len(chunks) >= 2:
            # 第一个 chunk 的末尾与第二个 chunk 的开头重叠
            end_of_first = chunks[0].content[-overlap:]
            start_of_second = chunks[1].content[:overlap]
            assert end_of_first == start_of_second

    def test_chunk_text_empty(self):
        """空文本 → 空列表"""
        chunks = chunk_text("", chunk_size=500, overlap=50)
        assert chunks == []

    def test_chunk_text_invalid_params(self):
        """chunk_size <= overlap → 报错"""
        with pytest.raises(ValueError, match="must be greater than overlap"):
            chunk_text("hello", chunk_size=10, overlap=10)

    def test_chunk_text_chunk_index_sequential(self, long_text):
        """chunk_index 从 0 递增"""
        chunks = chunk_text(long_text, chunk_size=500, overlap=50)
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i

    def test_chunk_text_metadata(self, long_text):
        """metadata 包含切分参数"""
        chunks = chunk_text(long_text, chunk_size=500, overlap=50)
        for chunk in chunks:
            assert chunk.metadata["chunk_size"] == 500
            assert chunk.metadata["overlap"] == 50
            assert "start_char" in chunk.metadata
            assert "end_char" in chunk.metadata


class TestChunkDocument:
    """Document 级别切分测试"""

    def test_chunk_document_links_document_id(self, sample_document):
        """验证 chunk 关联到正确的 document_id"""
        chunks = chunk_document(sample_document, chunk_size=200, overlap=20)
        for chunk in chunks:
            assert chunk.document_id == sample_document.document_id


# ============================================================
# Integration Tests
# ============================================================

class TestParseFile:
    """parse_file 集成测试"""

    @pytest.mark.asyncio
    async def test_parse_file_txt(self, tmp_path):
        file = tmp_path / "doc.txt"
        content = "Hello World. " * 300
        file.write_text(content, encoding="utf-8")
        chunks = await parse_file(str(file), chunk_size=500, overlap=50)
        assert len(chunks) > 0
        for chunk in chunks:
            assert isinstance(chunk, DocumentChunk)
            assert chunk.document_id != ""
            assert len(chunk.content) <= 500

    @pytest.mark.asyncio
    async def test_parse_file_md(self, tmp_path):
        file = tmp_path / "doc.md"
        content = "# Title\n\nContent. " * 300
        file.write_text(content, encoding="utf-8")
        chunks = await parse_file(str(file), chunk_size=500, overlap=50)
        assert len(chunks) > 0
        assert all(isinstance(c, DocumentChunk) for c in chunks)

    @pytest.mark.asyncio
    async def test_parse_file_clean_and_chunk(self, tmp_path):
        file = tmp_path / "dirty.txt"
        content = "Line 1\x00\r\n\n\n\n\n\nLine 2\n"
        file.write_text(content, encoding="utf-8")
        chunks = await parse_file(str(file), chunk_size=500, overlap=50)
        full_text = "".join(c.content for c in chunks)
        assert "\x00" not in full_text
