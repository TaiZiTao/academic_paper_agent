"""下载器: 五级降级策略。

L1 arXiv/S2 开放 PDF 直链 → L1.5 免费论文源兜底(free_pdf: arXiv 预印本 +
CVF Open Access + ACL Anthology + PMLR + NeurIPS + OpenReview + AAAI)
→ L2 Unpaywall OA 镜像 → L3 VPN 浏览器(Playwright, 默认停用,
enable_vpn_download=True 才走) → L4 失败(付费墙手动引导文案)。
"""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from loguru import logger

from app.research import free_pdf
from app.research.schemas import ImportItem

# 单次 PDF 下载总超时: arXiv 大文件(20MB+)在慢速网络下可拖到 100s+,
# 限定总时长让链路快速降级到免费源(CVF/ACL 等通常更快)
_FETCH_TOTAL_TIMEOUT = 45.0

UNPAYWALL_API = "https://api.unpaywall.org/v2"

# Windows 保留设备名(去扩展名后比较, 大小写不敏感)
_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


@dataclass
class DownloadResult:
    ok: bool
    level: str
    path: str | None = None
    message: str = ""


@dataclass
class Downloader:
    client: httpx.AsyncClient
    unpaywall_email: str = ""
    browser: object | None = None
    delay: float = 0.0
    enable_vpn_download: bool = False  # L3 VPN 浏览器下载开关: 默认停用, 付费墙走手动引导
    free_pdf_lookup: bool = True  # L1.5 免费论文源兜底开关(arXiv/ACL/PMLR/NeurIPS/OpenReview/AAAI/CVF)

    async def download(self, item: ImportItem, dest_dir: Path) -> DownloadResult:
        dest_dir.mkdir(parents=True, exist_ok=True)
        if item.pdf_url:
            try:
                path = await asyncio.wait_for(
                    self._fetch_pdf(item.pdf_url, dest_dir), timeout=_FETCH_TOTAL_TIMEOUT
                )
                if path is not None:
                    return DownloadResult(ok=True, level="L1", path=str(path))
            except (asyncio.TimeoutError, Exception) as exc:
                logger.warning(f"L1 下载失败({type(exc).__name__}): {exc}")
        # L1.5: 免费论文源兜底(free_pdf 按 venue 路由 arXiv 预印本 / CVF Open
        # Access / ACL Anthology / PMLR / NeurIPS / OpenReview / AAAI 等可直连
        # 免费渠道, 路由未识别或失败时统一 arXiv 兜底), 在 L2 前多试一次)
        if self.free_pdf_lookup:
            try:
                url = await free_pdf.find_free_pdf(
                    item.title, venue=item.venue or "", year=item.year
                )
                if url:
                    path = await asyncio.wait_for(
                        self._fetch_pdf(url, dest_dir), timeout=_FETCH_TOTAL_TIMEOUT
                    )
                    if path is not None:
                        return DownloadResult(ok=True, level="L1.5", path=str(path))
            except (asyncio.TimeoutError, Exception) as exc:
                logger.warning(f"L1.5 下载失败({type(exc).__name__}): {exc}")
        if item.doi and self.unpaywall_email:
            try:
                path = await asyncio.wait_for(
                    self._unpaywall(item.doi, dest_dir), timeout=_FETCH_TOTAL_TIMEOUT
                )
                if path is not None:
                    return DownloadResult(ok=True, level="L2", path=str(path))
            except (asyncio.TimeoutError, Exception) as exc:
                logger.warning(f"L2 下载失败({type(exc).__name__}): {exc}")
        if self.enable_vpn_download and self.browser is not None and item.page_url:
            try:
                path = await self.browser.download_pdf(item.page_url, dest_dir)
                if path is not None:
                    return DownloadResult(ok=True, level="L3", path=str(path))
            except Exception as exc:
                logger.warning(f"L3 下载失败: {exc}")
        return DownloadResult(
            ok=False, level="L4",
            message=f"该论文为付费墙文献，请通过学校网络/VPN 访问后手动下载，再拖入论文库。论文页: {item.page_url or ''} (DOI: {item.doi or '无'})",
        )

    async def _fetch_pdf(self, url: str, dest_dir: Path) -> Path | None:
        filename = _unique_filename(_safe_filename(url), dest_dir)
        target = dest_dir / filename
        try:
            async with self.client.stream("GET", url, follow_redirects=True) as resp:
                resp.raise_for_status()
                # 缓冲前 1024 字节校验 PDF 魔数, 拒绝登录墙/错误页返回的 200 HTML
                it = resp.aiter_bytes()
                head = b""
                while len(head) < 1024:
                    try:
                        chunk = await anext(it)
                    except StopAsyncIteration:
                        break
                    if chunk:
                        head += chunk
                if not head.startswith(b"%PDF-"):
                    return None
                with open(target, "wb") as f:
                    f.write(head)
                    async for chunk in it:
                        f.write(chunk)
            return target
        except asyncio.CancelledError:
            # wait_for 超时取消: 清理残留文件后继续向上抛(降级到下一级)
            target.unlink(missing_ok=True)
            raise
        except Exception:
            # 流中断/写失败时清理残留文件
            target.unlink(missing_ok=True)
            raise

    async def _unpaywall(self, doi: str, dest_dir: Path) -> Path | None:
        resp = await self.client.get(
            f"{UNPAYWALL_API}/{doi}",
            params={"email": self.unpaywall_email},
        )
        resp.raise_for_status()
        payload = resp.json()
        loc = payload.get("best_oa_location") or {}
        pdf_url = loc.get("url_for_pdf") or loc.get("url")
        if not pdf_url:
            return None
        return await self._fetch_pdf(pdf_url, dest_dir)


def _safe_filename(url: str) -> str:
    name = url.rsplit("/", 1)[-1].split("?")[0]
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    # Windows 禁止尾点/尾空格
    name = name.rstrip(" .")
    # 截断时保留 4 字符扩展名(如 .pdf), 避免切掉扩展名
    if len(name) > 180 and len(name) >= 4 and name[-4] == ".":
        name = name[:176] + name[-4:]
    elif len(name) > 180:
        name = name[:180]
    if not name or name in (".", ".."):
        return "paper.pdf"
    stem = name.rpartition(".")[0] if "." in name else name
    if stem.upper() in _RESERVED_NAMES:
        return "paper.pdf"
    return name


def _unique_filename(name: str, dest_dir: Path) -> str:
    """目标已存在时加序号后缀唯一化(name_1.pdf, name_2.pdf ...)。"""
    if not (dest_dir / name).exists():
        return name
    stem = Path(name).stem
    suffix = Path(name).suffix
    i = 1
    while True:
        candidate = f"{stem}_{i}{suffix}" if suffix else f"{stem}_{i}"
        if not (dest_dir / candidate).exists():
            return candidate
        i += 1
