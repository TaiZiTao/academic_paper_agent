"""Playwright 浏览器服务: 管理学校 VPN 会话与付费墙 PDF 下载。

设计: 首次登录由用户在「有头」浏览器手动完成(不存储密码), 会话持久化在
profile 目录; 之后复用会话自动导航论文页并触发下载。
Playwright 未安装时所有操作优雅降级为 none 状态。
"""

import asyncio
from pathlib import Path
from urllib.parse import urljoin, urlparse

from loguru import logger

from app.research.downloader import _unique_filename
from app.research.schemas import BrowserStatus

_SESSION_MARK = ".research_session_ok"
_PDF_MAGIC = b"%PDF-"
# 登录成功典型路径关键字(URL 命中任一即视为已离开登录态)
_LOGIN_OK_KEYWORDS = ("home", "dashboard", "index")
# 登录/统一认证页关键字(子串匹配): URL 命中任一说明仍停留在登录流程, 不算成功
# (如 CAS authserver/login、por/login 等, 门户可能 302 跳转到独立认证页)
_LOGIN_PAGE_KEYWORDS = ("authserver/login", "login_psw", "/login", "login?service", "por/login")
# 下载入口选择器(动态页面如 IEEE Xplore 按钮为异步渲染, 依次尝试)
_DOWNLOAD_SELECTORS = (
    "a[href*='.pdf']",
    "button:has-text('Download')",
    "a:has-text('Download PDF')",
    "a:has-text('PDF')",
    "button[class*='download']",
    "a[class*='download']",
    "[data-format='pdf']",
    "a[href*='document'] [class*='pdf']",
    "span:has-text('Download PDF')",
)
# PDF 直链兜底选择器(IEEE 常见 /stamp/stamp.jsp?tp=&arnumber=... 格式)
_PDF_LINK_SELECTORS = ("a[href*='stamp']", "a[href*='.pdf']")


def _is_login_page(url: str) -> bool:
    """URL 是否为登录/统一认证页(子串匹配, 大小写不敏感)。"""
    return bool(url) and any(k in url.lower() for k in _LOGIN_PAGE_KEYWORDS)
# 反爬敏感域名(子串匹配): 无头下载失败时改用有头模式重试
_ANTIBOT_DOMAINS = ("ieeexplore.ieee.org", "sciencedirect.com", "link.springer.com", "acm.org", "dl.acm.org")


def import_playwright():
    """延迟导入, 便于测试 monkeypatch。"""
    return __import__("playwright.async_api", fromlist=["async_playwright"])


class BrowserService:
    def __init__(self, profile_dir: Path, vpn_portal_url: str, timeout: float = 60.0):
        self.profile_dir = profile_dir
        self.vpn_portal_url = vpn_portal_url
        self.timeout = timeout
        self._pw = None
        self._browser = None
        self._context = None
        self._headless = None

    # ---------- 状态 ----------

    async def status(self) -> BrowserStatus:
        try:
            import_playwright()
        except ImportError:
            return BrowserStatus(status="none", message="Playwright 未安装, 请执行 pip install playwright && playwright install chromium")
        if self._browser is not None and not self._browser.is_connected():
            # 浏览器已断连: 连带清理 context 陈旧引用(_pw 由 close 统一管理)
            self._browser = None
            self._context = None
        if self._browser is not None:
            return BrowserStatus(status="alive", message="浏览器会话已连接")
        if (self.profile_dir / _SESSION_MARK).exists():
            return BrowserStatus(status="expired", message="会话已保存但浏览器未启动, 可调用 login 复用或重新登录")
        return BrowserStatus(status="none", message="尚未建立 VPN 会话")

    # ---------- 生命周期 ----------

    async def _ensure_browser(self, headless: bool):
        if self._browser is not None and self._browser.is_connected():
            if self._headless == headless:
                return
            # headless 模式不一致(如先 download_pdf 无头启动再 login): 关闭后按新模式重建
            await self.close()
        pw_mod = import_playwright()
        self._pw = await pw_mod.async_playwright().start()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._context = await self._pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=headless,
                accept_downloads=True,
                viewport={"width": 1280, "height": 900},
            )
        except Exception:
            # 启动失败回滚: 释放已启动的 playwright 实例, 避免 _pw 泄漏
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None
            self._headless = None
            raise
        self._browser = self._context.browser
        self._headless = headless

    async def login(self) -> BrowserStatus:
        """打开有头浏览器跳转 VPN 门户, 等待用户手动登录并保存会话。

        成功判定(两层):
        1. URL 相对变化: 用户完成登录必然发生页面跳转(即使仍停留在门户域名下,
           仅路径从登录页变为已登录页), 故轮询中 URL 与 goto 后初始 URL 不同
           且非异常页(about:blank / chrome-error://)即视为成功; 原「门户串不在
           URL」与「命中 home/dashboard/index 关键字」保留为补充 OR 条件。
        2. Cookie 增量兜底: 轮询超时后若门户域下出现新增 cookie, 视为已登录。
           注: 仅统计与门户主机名同域的 cookie 增量以降低登录页自身加载所设
           cookie 的误判; 登录页 CSRF 类 cookie 仍可能被计入, 属已知权衡。
        """
        try:
            await self._ensure_browser(headless=False)
            page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            await page.goto(self.vpn_portal_url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            try:
                initial_url = page.url
            except Exception:
                initial_url = ""
            try:
                initial_cookies = {(c.get("name"), c.get("domain")) for c in await self._context.cookies()}
            except Exception:
                initial_cookies = set()
            for _ in range(int(self.timeout)):  # timeout 秒内每秒探测
                await asyncio.sleep(1)
                if len(self._context.pages) == 0:
                    break
                page = self._context.pages[0]
                try:
                    url = page.url
                except Exception:
                    break
                if not url or url == "about:blank":
                    continue  # 初始空白页/尚未跳转, 跳过本轮
                if self._is_login_success(url, initial_url):
                    self._mark_session()
                    logger.info(f"VPN 会话已建立: {url}")
                    return BrowserStatus(status="alive", message="VPN 登录完成, 会话已保存")
            # 轮询超时: cookie 增量兜底(判定口径见 docstring)
            if await self._session_cookie_added(initial_cookies):
                self._mark_session()
                logger.info("VPN 会话已建立: 检测到门户域会话 cookie 增量")
                return BrowserStatus(status="alive", message="VPN 登录完成, 会话已保存")
            return BrowserStatus(status="none", message="VPN 登录超时或未完成，请重新尝试")
        except Exception as exc:
            logger.exception("VPN 登录失败")
            return BrowserStatus(status="none", message=f"VPN 登录失败: {exc}")

    def _is_login_success(self, url: str, initial_url: str) -> bool:
        """URL 层面判定登录是否成功(异常页与登录/认证页永远不算)。"""
        if not url or url == "about:blank" or url.startswith("chrome-error://"):
            return False
        if _is_login_page(url):
            return False  # 仍在登录/统一认证页(如 CAS authserver/login), 不算成功
        if url != initial_url:
            return True  # 已发生页面跳转(即使仍停留在门户域名下)
        if self.vpn_portal_url not in url:
            return True  # 已离开门户域(补充判定)
        return any(k in url.lower() for k in _LOGIN_OK_KEYWORDS)

    def _mark_session(self) -> None:
        """写入会话持久化标记(目录可能尚未创建, 先建目录)。"""
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        (self.profile_dir / _SESSION_MARK).write_text("ok", encoding="utf-8")

    async def _session_cookie_added(self, initial_keys: set) -> bool:
        """轮询超时后检查门户域下是否有新增 cookie(口径见 login docstring)。"""
        try:
            current = await self._context.cookies()
        except Exception:
            return False
        portal_host = self._portal_host(self.vpn_portal_url)
        if not portal_host:
            return False
        for c in current:
            domain = (c.get("domain") or "").lstrip(".")
            if domain != portal_host and not domain.endswith("." + portal_host):
                continue
            if (c.get("name"), c.get("domain")) not in initial_keys:
                return True
        return False

    @staticmethod
    def _portal_host(portal_url: str) -> str:
        return (urlparse(portal_url).hostname or "").lower()

    async def verify(self) -> BrowserStatus:
        """验证 VPN 会话: 仅「跳转到登录/认证页」或「异常页」判定失效。

        已登录用户访问门户可能 302 到 CAS(URL 仍含门户域)或进入
        portal/#!/service 资源页(也含门户域)——这些均视为会话有效。
        """
        if self._browser is None:
            return await self.status()
        try:
            page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            await page.goto(self.vpn_portal_url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
        except Exception as exc:
            # 网络异常/超时按失效处理, 但注明可能为网络问题
            return BrowserStatus(status="expired", message=f"VPN 会话验证失败(可能为网络问题): {exc}")
        try:
            url = page.url
        except Exception:
            url = ""
        if not url or url == "about:blank" or url.startswith("chrome-error://"):
            return BrowserStatus(status="expired", message="VPN 会话验证失败, 页面异常(about:blank/chrome-error)")
        if _is_login_page(url):
            return BrowserStatus(status="expired", message="VPN 会话已失效, 仍停留在认证登录页")
        # 门户资源页 / 离开门户域 / 其余页面一律视为会话有效
        return BrowserStatus(status="alive", message="VPN 会话有效")

    async def close(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._context = None
        self._browser = None
        self._pw = None
        self._headless = None

    # ---------- 下载 ----------

    async def download_pdf(self, page_url: str, dest_dir: Path) -> Path | None:
        """在 VPN 会话中打开论文页并触发 PDF 下载。

        反爬策略: 先无头尝试; 若失败且目标站点命中 _ANTIBOT_DOMAINS(如 IEEE
        Xplore 对 headless 返回 418 Unusual Traffic Detected), 关闭无头
        context 后改用有头模式重试一次(会话 cookie 已持久化在 profile 目录,
        重建后仍复用同一 VPN 会话)。有头模式会弹出浏览器窗口——这是预期行为。
        Playwright 未安装或任何步骤失败时返回 None, 不抛出异常(L4 兜底)。
        """
        target = await self._try_download(page_url, dest_dir, headless=True)
        if target is not None:
            return target
        if not any(domain in page_url for domain in _ANTIBOT_DOMAINS):
            return None
        logger.info("L3 无头下载失败, 目标站点反爬敏感, 改用有头重试")
        await self.close()
        return await self._try_download(page_url, dest_dir, headless=False)

    async def _try_download(self, page_url: str, dest_dir: Path, headless: bool) -> Path | None:
        """打开页面 + 触发下载 + 保存文件(单次尝试, 不做重试)。

        动态页面(如 IEEE Xplore)下载按钮为异步渲染: 先等页面稳定, 再依次
        尝试点击下载入口; 若均未触发下载事件, 兜底直接导航到 PDF 直链
        (如 /stamp/stamp.jsp?tp=&arnumber=...)捕获下载。
        """
        page = None
        try:
            await self._ensure_browser(headless=headless)
            dest_dir.mkdir(parents=True, exist_ok=True)
            page = await self._context.new_page()
            await page.goto(page_url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            # 等动态按钮渲染完成
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                await asyncio.sleep(3)  # networkidle 超时则固定等待
            # 1) 点击下载入口触发下载事件
            clickable = None
            for selector in _DOWNLOAD_SELECTORS:
                locator = page.locator(selector).first
                if await locator.count() and await locator.is_visible():
                    clickable = locator
                    break
            if clickable is not None:
                try:
                    async with page.expect_download(timeout=60_000) as dl_info:
                        await clickable.click()
                    return await self._save_download(await dl_info.value, dest_dir)
                except Exception:
                    pass  # 点击未触发下载事件, 继续直链兜底
            # 2) 直链兜底: 导航到 PDF 直链(stamp.jsp 等)捕获下载
            for link_sel in _PDF_LINK_SELECTORS:
                locator = page.locator(link_sel).first
                if not await locator.count():
                    continue
                href = await locator.get_attribute("href")
                if not href:
                    continue
                target_url = href if href.startswith("http") else urljoin(page.url, href)
                try:
                    async with page.expect_download(timeout=60_000) as dl_info:
                        await page.goto(target_url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                    return await self._save_download(await dl_info.value, dest_dir)
                except Exception:
                    continue  # 该直链未触发下载, 尝试下一个
            return None
        except Exception as exc:
            logger.warning(f"VPN 下载失败 {page_url}: {exc}")
            return None
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    async def _save_download(self, download, dest_dir: Path) -> Path | None:
        """保存下载文件并校验 PDF 魔数(非 PDF 删除)。"""
        try:
            target = dest_dir / _unique_filename(download.suggested_filename or "download.pdf", dest_dir)
            await download.save_as(str(target))
            if not self._looks_like_pdf(target):
                target.unlink(missing_ok=True)
                logger.warning(f"VPN 下载内容非 PDF, 已清理: {target.name}")
                return None
            return target
        except Exception as exc:
            logger.warning(f"VPN 下载保存失败: {exc}")
            return None

    @staticmethod
    def _looks_like_pdf(path: Path) -> bool:
        """校验前 1024 字节内是否含 PDF 魔数 %PDF-(拒绝登录墙/错误页 HTML)。"""
        try:
            with open(path, "rb") as fh:
                return _PDF_MAGIC in fh.read(1024)
        except OSError:
            return False
