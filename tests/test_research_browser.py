"""BrowserService 状态机测试(Playwright 未安装/未启动时返回 none)。"""

from pathlib import Path

import pytest

from app.research.browser import BrowserService

VPN_PORTAL = "https://vpn.swjtu.edu.cn"
LOGIN_URL = "https://vpn.swjtu.edu.cn/por/login"
LOGGED_IN_URL = "https://vpn.swjtu.edu.cn/portal/main"
CAS_LOGIN_URL = "https://cas-swjtu-edu-cn-s.vpn.swjtu.edu.cn:8118/authserver/login?service=https%3A%2F%2Fvpn.swjtu.edu.cn"


class FakePage:
    """按读取次数依次返回 URL 的假 page(第 1 次读为 goto 后初始 URL)。"""

    def __init__(self, urls):
        self._urls = list(urls)
        self._reads = 0

    @property
    def url(self):
        url = self._urls[min(self._reads, len(self._urls) - 1)]
        self._reads += 1
        return url

    async def goto(self, url, wait_until=None, timeout=None):
        pass


class FakeContext:
    """假 context: pages/new_page/cookies 均脚本化, 不触碰真实 playwright。"""

    def __init__(self, page, cookies_seq):
        self._page = page
        self._cookies_seq = [list(c) for c in cookies_seq]
        self._cookie_reads = 0
        self.pages = []

    async def new_page(self):
        self.pages.append(self._page)
        return self._page

    async def cookies(self):
        cookies = self._cookies_seq[min(self._cookie_reads, len(self._cookies_seq) - 1)]
        self._cookie_reads += 1
        return cookies


class FakeLocator:
    """假 locator: count/is_visible/click/get_attribute 脚本化。"""

    def __init__(self, page, found=False, visible=True, href=None):
        self._page = page
        self._found = found
        self._visible = visible
        self._href = href

    @property
    def first(self):
        return self

    async def count(self):
        return 1 if self._found else 0

    async def is_visible(self):
        return self._visible

    async def click(self):
        self._page.clicked = True

    async def get_attribute(self, name):
        return self._href


class FakeDownloadPage:
    """支持 wait_for_load_state/locator/expect_download 的假 page(下载流程)。"""

    def __init__(self, selectors, download, url="https://example.com/paper"):
        self._selectors = selectors  # {selector: dict(found/visible/href)}
        self._download = download
        self.url = url
        self.goto_calls = []
        self.clicked = False

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append(url)

    async def wait_for_load_state(self, state, timeout=None):
        pass

    async def close(self):
        pass

    def locator(self, selector):
        spec = self._selectors.get(selector) or {}
        return FakeLocator(self, **spec)

    def expect_download(self, timeout=None):
        return FakeDownloadExpect(self, self._download)


class FakeDownloadExpect:
    """模拟 expect_download: 块内既无点击也无导航则抛 TimeoutError。"""

    def __init__(self, page, download):
        self._page = page
        self._download = download

    async def __aenter__(self):
        self._goto_before = len(self._page.goto_calls)
        return self

    async def __aexit__(self, *exc):
        if not self._page.clicked and len(self._page.goto_calls) == self._goto_before:
            raise TimeoutError("Timeout waiting for download event")
        return False

    @property
    def value(self):
        # 与 Playwright 一致: value 是属性, await 得到 download 对象
        async def _resolve():
            return self._download
        return _resolve()


class FakeDownload:
    def __init__(self, filename, content):
        self.suggested_filename = filename
        self._content = content

    async def save_as(self, path):
        Path(path).write_bytes(self._content)


async def _noop_sleep(_):
    return None


async def _noop_ensure(headless):
    return None


def make_login_service(monkeypatch, tmp_path, page_urls, cookies_seq, timeout=2):
    """注入 stub 的 BrowserService: 跳过真实 playwright, 睡眠置为 noop。"""
    monkeypatch.setattr("app.research.browser.asyncio.sleep", _noop_sleep)
    service = BrowserService(profile_dir=tmp_path / "profile", vpn_portal_url=VPN_PORTAL, timeout=timeout)
    service._context = FakeContext(FakePage(page_urls), cookies_seq)

    async def noop_ensure(headless):
        return None

    monkeypatch.setattr(service, "_ensure_browser", noop_ensure)
    return service


@pytest.mark.asyncio
async def test_login_success_on_url_change(monkeypatch, tmp_path):
    # URL 从登录页变为门户域下另一路径(仍含门户串) → 判定成功
    service = make_login_service(monkeypatch, tmp_path, [LOGIN_URL, LOGGED_IN_URL], [[]])
    status = await service.login()
    assert status.status == "alive"
    assert (tmp_path / "profile" / ".research_session_ok").exists()


@pytest.mark.asyncio
async def test_login_success_on_cookie_added(monkeypatch, tmp_path):
    # URL 始终不变, 但轮询后门户域新增会话 cookie → 兜底判定成功
    service = make_login_service(
        monkeypatch, tmp_path,
        [LOGIN_URL],
        [[], [{"name": "JSESSIONID", "domain": ".vpn.swjtu.edu.cn", "value": "abc"}]],
    )
    status = await service.login()
    assert status.status == "alive"
    assert (tmp_path / "profile" / ".research_session_ok").exists()


@pytest.mark.asyncio
async def test_login_timeout_no_cookie(monkeypatch, tmp_path):
    # URL 不变且无新增 cookie → 超时返回 none, 不写标记
    service = make_login_service(monkeypatch, tmp_path, [LOGIN_URL], [[]])
    status = await service.login()
    assert status.status == "none"
    assert not (tmp_path / "profile" / ".research_session_ok").exists()


@pytest.mark.asyncio
async def test_login_ignores_chrome_error(monkeypatch, tmp_path):
    # URL 变为浏览器错误页 chrome-error:// → 不算成功, 轮询至超时返回 none
    service = make_login_service(monkeypatch, tmp_path, [LOGIN_URL, "chrome-error://chromewebdata/"], [[]])
    status = await service.login()
    assert status.status == "none"
    assert not (tmp_path / "profile" / ".research_session_ok").exists()


@pytest.mark.asyncio
async def test_login_redirect_to_cas_login_is_not_success(monkeypatch, tmp_path):
    # 门户 302 跳转到 CAS 统一认证登录页(URL 变化但仍在登录流程) → 不算成功, 不写标记
    service = make_login_service(monkeypatch, tmp_path, [LOGIN_URL, CAS_LOGIN_URL], [[]])
    status = await service.login()
    assert status.status == "none"
    assert not (tmp_path / "profile" / ".research_session_ok").exists()


def make_verify_service(tmp_path, url):
    """构造已连接(_browser 非 None)的 service, 注入 FakePage 返回指定 URL。"""
    service = BrowserService(profile_dir=tmp_path / "profile", vpn_portal_url=VPN_PORTAL)
    service._browser = object()
    service._context = FakeContext(FakePage([url]), [[]])
    return service


@pytest.mark.asyncio
async def test_verify_alive_on_portal_resource_page(tmp_path):
    # 已登录用户访问门户进入资源页(portal/#!/service, 仍含门户域) → alive
    service = make_verify_service(tmp_path, "https://vpn.swjtu.edu.cn/portal/#!/service")
    status = await service.verify()
    assert status.status == "alive"


@pytest.mark.asyncio
async def test_verify_expired_on_cas_login_page(tmp_path):
    # 跳转到 CAS 统一认证登录页 → expired
    service = make_verify_service(tmp_path, CAS_LOGIN_URL)
    status = await service.verify()
    assert status.status == "expired"


@pytest.mark.asyncio
async def test_verify_expired_on_blank_page(tmp_path):
    # 异常页 about:blank → expired
    service = make_verify_service(tmp_path, "about:blank")
    status = await service.verify()
    assert status.status == "expired"


@pytest.mark.asyncio
async def test_status_none_when_playwright_missing(monkeypatch, tmp_path):
    def raise_import():
        raise ImportError("no playwright")

    monkeypatch.setattr("app.research.browser.import_playwright", raise_import)
    service = BrowserService(profile_dir=tmp_path / "profile", vpn_portal_url=VPN_PORTAL)
    status = await service.status()
    assert status.status == "none"
    assert "Playwright" in status.message


@pytest.mark.asyncio
async def test_close_without_start_is_noop(tmp_path):
    service = BrowserService(profile_dir=tmp_path / "profile", vpn_portal_url=VPN_PORTAL)
    await service.close()  # 未启动时不抛异常
    assert service._context is None
    assert service._browser is None
    assert service._pw is None


@pytest.mark.asyncio
async def test_download_pdf_returns_none_when_playwright_missing(monkeypatch, tmp_path):
    def raise_import():
        raise ImportError("no playwright")

    monkeypatch.setattr("app.research.browser.import_playwright", raise_import)
    service = BrowserService(profile_dir=tmp_path / "profile", vpn_portal_url=VPN_PORTAL)
    out = await service.download_pdf("https://example.com/paper", tmp_path / "out")
    assert out is None  # 契约: 未安装时返回 None 而非抛错


@pytest.mark.asyncio
async def test_download_pdf_retries_headed_for_antibots(monkeypatch, tmp_path):
    # page_url 命中反爬域名: 无头首次返回 None → 关闭无头 context 后有头重试并最终返回路径
    calls = []
    out_path = tmp_path / "out" / "ieee.pdf"
    service = BrowserService(profile_dir=tmp_path / "profile", vpn_portal_url=VPN_PORTAL)

    async def fake_try_download(page_url, dest_dir, headless):
        calls.append((page_url, dest_dir, headless))
        if headless:
            return None
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF-1.7 fake")
        return out_path

    closed = []

    async def fake_close():
        closed.append(1)

    monkeypatch.setattr(service, "_try_download", fake_try_download)
    monkeypatch.setattr(service, "close", fake_close)
    monkeypatch.setattr(service, "_ensure_browser", _noop_ensure)

    target = await service.download_pdf("https://ieeexplore.ieee.org/document/123", tmp_path / "out")
    assert target == out_path
    assert [h for _, _, h in calls] == [True, False]  # 无头失败后有头重试
    assert len(closed) == 1  # 有头重试前关闭无头 context


@pytest.mark.asyncio
async def test_download_pdf_no_retry_for_normal_domain(monkeypatch, tmp_path):
    # 普通域名(arxiv.org): 无头失败直接返回 None, 不做有头重试
    calls = []
    service = BrowserService(profile_dir=tmp_path / "profile", vpn_portal_url=VPN_PORTAL)

    async def fake_try_download(page_url, dest_dir, headless):
        calls.append((page_url, dest_dir, headless))
        return None

    monkeypatch.setattr(service, "_try_download", fake_try_download)
    monkeypatch.setattr(service, "_ensure_browser", _noop_ensure)

    target = await service.download_pdf("https://arxiv.org/abs/2401.00001", tmp_path / "out")
    assert target is None
    assert len(calls) == 1
    assert calls[0][2] is True  # 仅无头一次


@pytest.mark.asyncio
async def test_try_download_clicks_ieee_download_button(monkeypatch, tmp_path):
    # IEEE 动态页: 新增选择器 button[class*='download'] 命中 → 点击并保存
    page = FakeDownloadPage(
        selectors={"button[class*='download']": {"found": True, "visible": True}},
        download=FakeDownload("ieee.pdf", b"%PDF-1.7 fake"),
    )
    service = BrowserService(profile_dir=tmp_path / "profile", vpn_portal_url=VPN_PORTAL)
    monkeypatch.setattr(service, "_ensure_browser", _noop_ensure)
    service._context = FakeContext(page, [[]])
    target = await service._try_download("https://ieeexplore.ieee.org/document/123", tmp_path / "out", headless=True)
    assert target is not None and target.name == "ieee.pdf"
    assert page.clicked


@pytest.mark.asyncio
async def test_try_download_goto_pdf_link_fallback(monkeypatch, tmp_path):
    # 无下载按钮可点 → 兜底导航到 stamp 直链并捕获下载
    page = FakeDownloadPage(
        selectors={"a[href*='stamp']": {"found": True, "href": "/stamp/stamp.jsp?tp=&arnumber=123"}},
        download=FakeDownload("paper.pdf", b"%PDF-1.7 fake"),
    )
    service = BrowserService(profile_dir=tmp_path / "profile", vpn_portal_url=VPN_PORTAL)
    monkeypatch.setattr(service, "_ensure_browser", _noop_ensure)
    service._context = FakeContext(page, [[]])
    target = await service._try_download("https://ieeexplore.ieee.org/document/123", tmp_path / "out", headless=True)
    assert target is not None and target.name == "paper.pdf"
    assert any("stamp" in c for c in page.goto_calls)


@pytest.mark.asyncio
async def test_status_expired_with_marker(monkeypatch, tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / ".research_session_ok").write_text("ok", encoding="utf-8")
    monkeypatch.setattr("app.research.browser.import_playwright", lambda: object())
    service = BrowserService(profile_dir=profile, vpn_portal_url=VPN_PORTAL)
    status = await service.status()
    assert status.status == "expired"


def test_looks_like_pdf(tmp_path):
    good = tmp_path / "good.pdf"
    good.write_bytes(b"%PDF-1.7 fake")
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"<html>not a pdf</html>")
    assert BrowserService._looks_like_pdf(good)
    assert not BrowserService._looks_like_pdf(bad)
