"""下载器五级降级测试(全部 mock)。L1 直链 → L1.5 免费论文源(free_pdf) → L2 Unpaywall → L3 VPN 浏览器 → L4 付费墙。"""

from pathlib import Path

import httpx
import pytest

from app.research.downloader import Downloader
from app.research.schemas import ImportItem


class FakeBrowser:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    async def download_pdf(self, page_url: str, dest_dir: Path):
        self.calls.append((page_url, dest_dir))
        if not self.ok:
            return None
        target = dest_dir / "vpn.pdf"
        target.write_bytes(b"%PDF-vpn")
        return target


def make_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def pdf_bytes() -> bytes:
    return b"%PDF-1.4 fake"


class FakeFreePdf:
    """L1.5 兜底假实现: 记录调用(title, venue, year)并返回预设 PDF URL(或 None)。"""

    def __init__(self, result: str | None = None):
        self.result = result
        self.calls: list[tuple[str, str, int | None]] = []

    async def find_free_pdf(self, title: str, venue: str = "", year: int | None = None) -> str | None:
        self.calls.append((title, venue, year))
        return self.result


@pytest.fixture(autouse=True)
def fake_free_pdf(monkeypatch):
    """默认注入 FakeFreePdf(返回 None): 现有测试不触发真实网络, L1.5 专属测试再改 result。"""
    fake = FakeFreePdf(result=None)
    import app.research.free_pdf as free_pdf_mod
    monkeypatch.setattr(free_pdf_mod, "find_free_pdf", fake.find_free_pdf)
    return fake


@pytest.mark.asyncio
async def test_l1_direct_pdf():
    def handler(request):
        assert "files.example.com" in str(request.url)
        return httpx.Response(200, content=pdf_bytes())

    d = Downloader(client=make_client(handler), unpaywall_email="")
    item = ImportItem(source="arxiv", title="T", pdf_url="https://files.example.com/p.pdf", page_url="https://arxiv.org/abs/1")
    out = await d.download(item, Path("tmp"))
    assert out.ok and out.level == "L1"
    assert Path(out.path).read_bytes() == pdf_bytes()


@pytest.mark.asyncio
async def test_l2_unpaywall_fallback():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:  # L1 的 pdf_url 失败
            return httpx.Response(404)
        if "api.unpaywall.org" in str(request.url):  # L2 命中
            return httpx.Response(200, json={"best_oa_location": {"url_for_pdf": "https://oa.example.com/p.pdf"}})
        return httpx.Response(200, content=pdf_bytes())

    d = Downloader(client=make_client(handler), unpaywall_email="me@example.com")
    item = ImportItem(source="arxiv", title="T", doi="10.1000/xyz", pdf_url="https://bad.example.com/p.pdf", page_url="https://x.org")
    out = await d.download(item, Path("tmp"))
    assert out.ok and out.level == "L2"


@pytest.mark.asyncio
async def test_l3_vpn_browser_fallback():
    def handler(request):
        if "api.unpaywall.org" in str(request.url):
            return httpx.Response(200, json={"best_oa_location": None})
        return httpx.Response(404)

    browser = FakeBrowser(ok=True)
    d = Downloader(client=make_client(handler), unpaywall_email="me@example.com", browser=browser, enable_vpn_download=True)
    item = ImportItem(source="arxiv", title="T", doi="10.1000/xyz", page_url="https://ieeexplore.ieee.org/document/1")
    out = await d.download(item, Path("tmp"))
    assert out.ok and out.level == "L3"
    assert Path(out.path).read_bytes() == b"%PDF-vpn"


@pytest.mark.asyncio
async def test_l3_disabled_by_default_skips_browser():
    """enable_vpn_download 默认 False: L1/L2 失败时不再调用浏览器, 直接落 L4。"""
    def handler(request):
        if "api.unpaywall.org" in str(request.url):
            return httpx.Response(200, json={"best_oa_location": None})
        return httpx.Response(404)

    browser = FakeBrowser(ok=True)
    d = Downloader(client=make_client(handler), unpaywall_email="me@example.com", browser=browser)
    item = ImportItem(source="arxiv", title="T", doi="10.1000/xyz", page_url="https://ieeexplore.ieee.org/document/1")
    out = await d.download(item, Path("tmp"))
    assert not out.ok and out.level == "L4"
    assert browser.calls == []  # L3 未被触发


@pytest.mark.asyncio
async def test_l3_runs_when_vpn_download_enabled():
    """enable_vpn_download=True 且 browser/page_url 就绪时走 L3 浏览器下载。"""
    def handler(request):
        if "api.unpaywall.org" in str(request.url):
            return httpx.Response(200, json={"best_oa_location": None})
        return httpx.Response(404)

    browser = FakeBrowser(ok=True)
    d = Downloader(client=make_client(handler), unpaywall_email="me@example.com", browser=browser, enable_vpn_download=True)
    item = ImportItem(source="arxiv", title="T", doi="10.1000/xyz", page_url="https://ieeexplore.ieee.org/document/1")
    out = await d.download(item, Path("tmp"))
    assert out.ok and out.level == "L3"
    assert len(browser.calls) == 1
    assert Path(out.path).read_bytes() == b"%PDF-vpn"


@pytest.mark.asyncio
async def test_l4_paywall_manual_guidance_message():
    """L4 失败消息为付费墙手动引导文案, 含「付费墙文献」与论文页/DOI。"""
    def handler(request):
        return httpx.Response(404)

    d = Downloader(client=make_client(handler), unpaywall_email="")
    item = ImportItem(source="arxiv", title="T", doi="10.1000/xyz", page_url="https://publisher.example.com/p")
    out = await d.download(item, Path("tmp"))
    assert not out.ok and out.level == "L4"
    assert "付费墙文献" in out.message
    assert "https://publisher.example.com/p" in out.message
    assert "10.1000/xyz" in out.message


@pytest.mark.asyncio
async def test_l4_all_fail():
    def handler(request):
        return httpx.Response(404)

    d = Downloader(client=make_client(handler), unpaywall_email="")
    item = ImportItem(source="arxiv", title="T", page_url="https://publisher.example.com/p")
    out = await d.download(item, Path("tmp"))
    assert not out.ok and out.level == "L4"

@pytest.mark.asyncio
async def test_l1_follows_redirect():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:  # L1 的 pdf_url 302 跳转
            return httpx.Response(302, headers={"Location": "https://cdn.example.com/p-v2.pdf"})
        return httpx.Response(200, content=pdf_bytes())

    d = Downloader(client=make_client(handler), unpaywall_email="")
    item = ImportItem(source="arxiv", title="T", pdf_url="https://files.example.com/p.pdf", page_url="https://arxiv.org/abs/1")
    out = await d.download(item, Path("tmp"))
    assert out.ok and out.level == "L1"
    assert calls["n"] == 2
    assert Path(out.path).read_bytes() == pdf_bytes()


@pytest.mark.asyncio
async def test_l1_rejects_html():
    def handler(request):
        url = str(request.url)
        if "api.unpaywall.org" in url:
            return httpx.Response(200, json={"best_oa_location": {"url_for_pdf": "https://oa.example.com/p.pdf"}})
        if "oa.example.com" in url:
            return httpx.Response(200, content=pdf_bytes())
        return httpx.Response(200, content=b"<html><body>sign in to view</body></html>")

    d = Downloader(client=make_client(handler), unpaywall_email="me@example.com")
    item = ImportItem(source="arxiv", title="T", doi="10.1000/xyz", pdf_url="https://bad.example.com/p.pdf", page_url="https://x.org")
    out = await d.download(item, Path("tmp"))
    assert out.ok and out.level == "L2"
    assert Path(out.path).read_bytes() == pdf_bytes()


def test_safe_filename_edge():
    from app.research.downloader import _safe_filename

    assert _safe_filename("https://x/CON") == "paper.pdf"
    assert _safe_filename("https://x/CON.pdf") == "paper.pdf"
    assert _safe_filename("https://x/.") == "paper.pdf"
    assert _safe_filename("https://x/..") == "paper.pdf"
    long_name = _safe_filename("https://x/" + "a" * 200 + ".pdf")
    assert len(long_name) == 180
    assert long_name.endswith(".pdf")


@pytest.mark.asyncio
async def test_filename_conflict_unique(tmp_path):
    # tmp_path 保证每测试独立目录, 不依赖全局 tmp/ 残留状态
    def handler(request):
        return httpx.Response(200, content=pdf_bytes())

    d = Downloader(client=make_client(handler), unpaywall_email="")
    item = ImportItem(source="arxiv", title="T", pdf_url="https://files.example.com/dup.pdf", page_url="https://arxiv.org/abs/1")
    out1 = await d.download(item, tmp_path)
    out2 = await d.download(item, tmp_path)
    assert out1.ok and out2.ok
    assert Path(out1.path).name == "dup.pdf"
    assert Path(out2.path).name == "dup_1.pdf"
    assert out1.path != out2.path

# ============================================================
# L1.5 免费论文源兜底(free_pdf)
# ============================================================


@pytest.mark.asyncio
async def test_l15_free_pdf_found(fake_free_pdf, tmp_path):
    """free_pdf 命中(如 CVPR 论文走 CVF): L1 失败后走 L1.5 拿到 PDF, venue/year 透传。"""
    fake_free_pdf.result = "https://openaccess.thecvf.com/content/CVPR2023/papers/X_Y_CVPR_2023_paper.pdf"

    def handler(request):
        url = str(request.url)
        if "api.unpaywall.org" in url:
            return httpx.Response(200, json={"best_oa_location": None})
        if "openaccess.thecvf.com" in url:
            return httpx.Response(200, content=pdf_bytes())
        return httpx.Response(404)

    d = Downloader(client=make_client(handler), unpaywall_email="me@example.com")
    item = ImportItem(
        source="openalex",
        title="An Image is Worth 16x16 Words",
        venue="CVPR 2023",
        year=2023,
        doi="10.1109/CVPR.2023.01234",
        pdf_url="https://bad.example.com/p.pdf",
        page_url="https://ieeexplore.ieee.org/document/1000",
    )
    out = await d.download(item, tmp_path)
    assert out.ok and out.level == "L1.5"
    assert Path(out.path).read_bytes() == pdf_bytes()
    assert fake_free_pdf.calls == [(item.title, "CVPR 2023", 2023)]  # venue/year 透传给 free_pdf


@pytest.mark.asyncio
async def test_l15_arxiv_fallback_for_non_cv_paper(fake_free_pdf, tmp_path):
    """非 CV 论文同样触发 L1.5(free_pdf 路由到 arXiv 等通用源), 命中即 L1.5。"""
    fake_free_pdf.result = "https://arxiv.org/pdf/2010.11929"

    def handler(request):
        url = str(request.url)
        if "api.unpaywall.org" in url:
            return httpx.Response(200, json={"best_oa_location": None})
        if "arxiv.org/pdf" in url:
            return httpx.Response(200, content=pdf_bytes())
        return httpx.Response(404)

    d = Downloader(client=make_client(handler), unpaywall_email="me@example.com")
    item = ImportItem(
        source="arxiv", title="T", venue="IEEE Transactions on Pattern Analysis and Machine Intelligence",
        year=2023, doi="10.1000/xyz",
        pdf_url="https://bad.example.com/p.pdf", page_url="https://arxiv.org/abs/1",
    )
    out = await d.download(item, tmp_path)
    assert out.ok and out.level == "L1.5"
    assert Path(out.path).read_bytes() == pdf_bytes()
    assert fake_free_pdf.calls == [(item.title, "IEEE Transactions on Pattern Analysis and Machine Intelligence", 2023)]


@pytest.mark.asyncio
async def test_l15_miss_falls_to_l2(fake_free_pdf, tmp_path):
    """free_pdf 未命中(返回 None): 继续 L2 Unpaywall。"""
    # fake_free_pdf.result 默认 None
    def handler(request):
        url = str(request.url)
        if "api.unpaywall.org" in url:
            return httpx.Response(200, json={"best_oa_location": {"url_for_pdf": "https://oa.example.com/p.pdf"}})
        if "oa.example.com" in url:
            return httpx.Response(200, content=pdf_bytes())
        return httpx.Response(404)

    d = Downloader(client=make_client(handler), unpaywall_email="me@example.com")
    item = ImportItem(
        source="openalex",
        title="T",
        venue="ICCV 2023",
        year=2023,
        doi="10.1109/ICCV.2023.01234",
        pdf_url="https://bad.example.com/p.pdf",
        page_url="https://ieeexplore.ieee.org/document/1",
    )
    out = await d.download(item, tmp_path)
    assert out.ok and out.level == "L2"
    assert Path(out.path).read_bytes() == pdf_bytes()
    assert fake_free_pdf.calls == [(item.title, "ICCV 2023", 2023)]


@pytest.mark.asyncio
async def test_l15_disabled_by_free_pdf_lookup_false(fake_free_pdf, tmp_path):
    """free_pdf_lookup=False 时即使有 venue 也不调 free_pdf。"""
    d = Downloader(client=make_client(lambda r: httpx.Response(404)), unpaywall_email="", free_pdf_lookup=False)
    item = ImportItem(
        source="openalex", title="T", venue="CVPR 2023", doi="10.1109/CVPR.2023.01234", page_url="https://x.org",
    )
    out = await d.download(item, tmp_path)
    assert not out.ok and out.level == "L4"
    assert fake_free_pdf.calls == []

