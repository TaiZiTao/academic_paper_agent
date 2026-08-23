<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Download, Refresh, Search, SwitchButton } from "@element-plus/icons-vue";
import {
  browserLogin,
  createImports,
  getBrowserStatus,
  listImports,
  retryImport,
  searchResearch,
} from "@/api/research";
import type { BrowserStatus, ImportTask, SearchResult, SearchSource } from "@/types/research";
import ImportQueueDrawer from "./ImportQueueDrawer.vue";

const query = ref("");
const yearMin = ref<number | null>(null); // 起始年, null = 不限
const yearMax = ref<number | null>(null); // 结束年, null = 不限
const searching = ref(false);
const paging = ref(false); // 页码切换加载中
const currentPage = ref(1);
const hasSearched = ref(false); // 是否已发起过检索（分页栏可见性依据, 与结果列表解耦）
const results = ref<SearchResult[]>([]);
const selected = ref<Set<number>>(new Set());
const processLines = ref<string[]>([]);
const queueVisible = ref(false);
const imports = ref<ImportTask[]>([]);
const vpnDialogVisible = ref(false);
const browserStatus = ref<BrowserStatus>({ status: "none", message: "" });
let pollTimer: number | undefined;
let abortCtrl: AbortController | null = null;
let searchSeq = 0; // 检索代际计数: 每次新检索 +1, 旧代际的收尾/回调不污染新代际
let disposed = false;

const pageSize = ref(20); // 每页条数(可切换 20/30/50, 后端 top_k 上限 50)
const PAGE_SIZE_OPTIONS = [20, 30, 50];
const loadedTotal = ref(0); // 后端报告的 total（估计上界）
const totalIsEstimate = ref(false); // total 是否为估计值
let seenKeys = new Set<string>(); // 去重 key（title 归一化 + doi/year/source）

// 年份筛选项: 1991 - 当前年（倒序, 最近的在前）
const currentYear = new Date().getFullYear();
const yearOptions = computed(() => {
  const years: number[] = [];
  for (let y = currentYear; y >= 1991; y--) years.push(y);
  return years;
});

// 最大页码（前端钳制, 最小 1）: 与后端 offset>=total 短路一致, 越界页不再真实请求
const maxPage = computed(() => Math.max(1, Math.ceil(loadedTotal.value / pageSize.value)));

// 是否还有下一页（页码导航语义: 按 currentPage 与 loadedTotal 推算）
const hasMore = computed(() => currentPage.value * pageSize.value < loadedTotal.value);

// 年份筛选是否生效（任一端设置了值）
const filterActive = computed(() => yearMin.value != null || yearMax.value != null);

/**
 * 年份实时过滤: 在已加载结果 results 上叠加年份 [yearMin, yearMax]。
 * 年份: year 在 [min, max] 区间保留; 未设置的一端不限; 无年份(year=null)的结果仅在未设年份筛选时显示。
 * 排序由后端固定: 已发表 CCF-A 优先 -> CCF-B 次之 -> ... -> 未发表最后(不做前端重排)。
 */
const filteredResults = computed(() => {
  const min = yearMin.value;
  const max = yearMax.value;
  return results.value.filter((r) => {
    if (r.year == null) {
      return !(min != null || max != null);
    }
    if (min != null && r.year < min) return false;
    if (max != null && r.year > max) return false;
    return true;
  });
});

// 结果摘要: 显示 X / 共 Y 条（X 为年份筛选后的展示数, Y 为后端报告总数; 检索/翻页中不显示避免瞬时失真）
const summaryLine = computed(() => {
  if (!hasSearched.value || searching.value || paging.value) return "";
  const count = filteredResults.value.length;
  const total = loadedTotal.value;
  return "显示 " + count + " / 共" + (totalIsEstimate.value ? "约 " : " ") + total + " 条";
});

// 空态三态: null = 不显示; 未搜索 / 确实无结果 / 页码越界(附「返回上一页」)
const emptyState = computed<{ text: string; outOfBounds: boolean } | null>(() => {
  if (searching.value || paging.value) return null;
  if (!hasSearched.value) return { text: "输入需求开始检索", outOfBounds: false };
  if (filteredResults.value.length > 0) return null;
  if (results.value.length > 0) {
    // 年份筛选生效时提示调整方向
    const label = filterActive.value ? "当前年份筛选下无结果" : "当前筛选下无结果";
    return { text: label + "，请调整年份或点击「搜索新结果」", outOfBounds: false };
  }
  if (loadedTotal.value > 0 && currentPage.value > 1) {
    return { text: "当前页码超出结果范围", outOfBounds: true };
  }
  return { text: "未找到相关结果", outOfBounds: false };
});

function pushProcess(line: string) {
  processLines.value.push(line);
}

/** 去重 key: title 归一化(小写去非字母数字) + doi/year/source 提高区分度; 空标题时 doi 兜底 */
function normKey(r: SearchResult): string {
  const title = (r.title || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  return [title, r.doi, r.year, r.source].filter(Boolean).join("|");
}

/** 来源小标文案（展示用） */
function sourceLabel(source: SearchSource): string {
  switch (source) {
    case "arxiv":
      return "arXiv";
    case "semantic_scholar":
      return "Semantic Scholar";
    case "openalex":
      return "OpenAlex";
    default:
      return source;
  }
}

/** 过滤出本页新增条目并登记 key（页内去重 + 与上一页重叠去重） */
function filterFresh(items: SearchResult[]): SearchResult[] {
  return items.filter((it) => {
    const key = normKey(it);
    if (seenKeys.has(key)) return false;
    seenKeys.add(key);
    return true;
  });
}

/** 过程行: 第 X 页 N 条 / 共约 Y 条（total 为估计值时用「约」） */
function progressLine(count: number, total: number, isEstimate: boolean): string {
  return "第 " + currentPage.value + " 页 " + count + " 条 / 共" + (isEstimate ? "约 " : " ") + total + " 条";
}

/** 统一超时 + 取消信号 */
function requestSignal(): AbortSignal | undefined {
  return abortCtrl ? AbortSignal.any([abortCtrl.signal, AbortSignal.timeout(300_000)]) : undefined;
}

/**
 * 以服务端回显 offset 校正当前页码（服务端可能因多源合并/去重每页实返 != pageSize）。
 * 服务端未回显 offset（undefined）时保持现状, 不做任何校正; 回显页码同时钳制在 maxPage 内。
 */
function syncPageFromOffset(resOffset: number | undefined) {
  if (typeof resOffset !== "number" || !Number.isFinite(resOffset) || resOffset < 0) return;
  const echoed = Math.floor(resOffset / pageSize.value) + 1;
  const target = Math.min(echoed, maxPage.value);
  if (target >= 1 && target !== currentPage.value) {
    currentPage.value = target;
  }
}

/**
 * 核心检索: 无并发 guard, 每次调用都会中止在途请求并用当前年份参数重启。
 * 通过 searchSeq 代际计数防止旧请求的收尾（finally/回调）污染新检索状态。
 */
async function runSearch() {
  const q = query.value.trim();
  if (!q) {
    ElMessage.warning("请输入检索需求");
    return;
  }
  const seq = ++searchSeq;
  abortCtrl?.abort();
  abortCtrl = new AbortController();
  searching.value = true;
  paging.value = false;
  currentPage.value = 1;
  results.value = [];
  selected.value = new Set();
  processLines.value = [];
  seenKeys = new Set();
  loadedTotal.value = 0;
  totalIsEstimate.value = false;
  hasSearched.value = true;
  try {
    await searchResearch(
      q,
      pageSize.value,
      0,
      { yearMin: yearMin.value, yearMax: yearMax.value, refresh: true, signal: requestSignal() },
      {
        onPlan: (queries, sources, direct) => {
          if (seq !== searchSeq) return;
          pushProcess("规划: " + queries.join(" | ") + " × [" + sources.join(", ") + "]" + (direct ? " (直查模式)" : ""));
        },
        onResults: (items, total, resOffset, isEstimate) => {
          if (disposed || seq !== searchSeq) return;
          const fresh = filterFresh(items);
          results.value.push(...fresh);
          loadedTotal.value = total;
          totalIsEstimate.value = !!isEstimate;
          syncPageFromOffset(resOffset);
          pushProcess(progressLine(fresh.length, total, !!isEstimate));
        },
        onError: (message) => {
          if (seq !== searchSeq) return;
          ElMessage.error(message);
        },
      },
    );
  } catch (error) {
    if (seq !== searchSeq) return; // 已被更新的检索取代, 丢弃旧结果
    if (error instanceof DOMException && error.name === "AbortError") return;
    if (error instanceof DOMException && error.name === "TimeoutError") {
      ElMessage.error("搜索超时，请稍后重试");
      return;
    }
    ElMessage.error(error instanceof Error ? error.message : "搜索失败");
  } finally {
    if (seq === searchSeq) searching.value = false;
  }
}

/** 搜索按钮: 在途时忽略重复点击 */
function doSearch() {
  if (searching.value || paging.value) return;
  void runSearch();
}

/**
 * 筛选变化（年份）: 仅前端实时过滤已加载结果, 不触发后端重搜（改年份后需点击「搜索新结果」才做后端过滤）。
 * 勾选索引基于显示列表(filteredResults), 筛选变化后索引语义改变, 故清空勾选避免误选。
 */
function onFilterChange() {
  selected.value = new Set();
}

/** 拉取一页并去重; 返回本页新增条目, null 表示本次拉取失败（已提示） */
async function fetchPage(q: string, offset: number, signal?: AbortSignal): Promise<SearchResult[] | null> {
  let fresh: SearchResult[] = [];
  let failed = false;
  let received = false;
  await searchResearch(
    q,
    pageSize.value,
    offset,
    { yearMin: yearMin.value, yearMax: yearMax.value, signal },
    {
      onResults: (items, total, resOffset, isEstimate) => {
        if (disposed) return;
        received = true;
        fresh = filterFresh(items);
        loadedTotal.value = total;
        totalIsEstimate.value = !!isEstimate;
        syncPageFromOffset(resOffset);
        pushProcess(progressLine(fresh.length, total, !!isEstimate));
      },
      onError: (message) => {
        failed = true;
        ElMessage.error(message);
      },
    },
  );
  if (failed || !received) return null;
  return fresh;
}

/** 每页条数切换: 以新条数重新发起检索(offset 回到 0, 旧结果作废) */
async function onSizeChange(_size: number) {
  if (searching.value || paging.value) return;
  if (!hasSearched.value || !query.value.trim()) return;
  await runSearch();
}

/** 页码导航: offset = (page-1)*pageSize, 重新请求该页并替换列表 */
async function onPageChange(page: number) {
  const q = query.value.trim();
  if (!q || searching.value || paging.value) return;
  // 前端钳制页码: 越界直接钳到末页（受控模式下写 currentPage 不触发 current-change, 无重入）
  const target = Math.min(page, maxPage.value);
  if (target !== page) {
    currentPage.value = target;
  }
  paging.value = true;
  try {
    const items = await fetchPage(q, (target - 1) * pageSize.value, requestSignal());
    if (disposed) return;
    if (items === null || items.length === 0) {
      // 空页/越界: 回退页码并保留原列表, 避免分页栏随空列表消失、用户失去翻回入口
      if (items !== null) {
        ElMessage.warning("该页暂无数据，已返回上一页");
      }
      currentPage.value = target <= 1 ? 1 : target - 1;
      return;
    }
    results.value = items;
    selected.value = new Set(); // 勾选索引基于旧页, 翻页后清空
    // 去重登记表随当前页重建（页码导航为替换语义, 仅保留当前页 key）
    seenKeys = new Set(items.map(normKey));
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") return;
    if (error instanceof DOMException && error.name === "TimeoutError") {
      ElMessage.error("翻页超时，请稍后重试");
    } else {
      ElMessage.error(error instanceof Error ? error.message : "翻页失败");
    }
    currentPage.value = target <= 1 ? 1 : target - 1;
  } finally {
    paging.value = false;
  }
}

/** 空态「返回上一页」 */
function goBackPage() {
  void onPageChange(Math.max(1, currentPage.value - 1));
}

function toggleSelect(index: number) {
  const next = new Set(selected.value);
  if (next.has(index)) next.delete(index);
  else next.add(index);
  selected.value = next;
}

const HTML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}

/** 付费墙论文列表 HTML: 标题 + 论文页链接(点击新窗口打开), 用于 ElMessageBox 展示 */
function paywalledListHtml(items: SearchResult[]): string {
  const rows = items.map((r) => {
    const title = escapeHtml(r.title);
    const link = r.page_url
      ? '<a href="' + escapeHtml(r.page_url) + '" target="_blank" rel="noopener">' + escapeHtml(r.page_url) + "</a>"
      : '<span style="color:#999">无论文页链接</span>';
    return '<div style="margin:8px 0 4px"><b>' + title + "</b><br/>" + link + "</div>";
  });
  return (
    "以下 " + items.length + " 篇论文为付费墙文献，请手动下载后拖入论文库：" +
    '<div style="margin-top:6px;text-align:left;max-height:320px;overflow:auto">' + rows.join("") + "</div>"
  );
}

async function downloadSelected() {
  if (selected.value.size === 0) {
    ElMessage.warning("请先勾选论文");
    return;
  }
  const selectedResults = [...selected.value]
    .map((i) => filteredResults.value[i])
    .filter((r): r is SearchResult => !!r);
  // 开放获取: 有 pdf 直链或标记 open → 自动下载入库; 其余(closed/unknown 无直链)为付费墙 → 手动引导
  const openResults = selectedResults.filter((r) => r.pdf_url || r.oa_status === "open");
  const paywalledResults = selectedResults.filter((r) => !(r.pdf_url || r.oa_status === "open"));
  const toImportItem = (r: SearchResult) => ({
    source: r.source,
    title: r.title,
    year: r.year,
    venue: r.venue,
    doi: r.doi,
    pdf_url: r.pdf_url,
    page_url: r.page_url,
    external_id: null,
  });
  try {
    if (openResults.length > 0) {
      await createImports(openResults.map(toImportItem));
      ElMessage.success("已提交 " + openResults.length + " 篇下载任务");
      await refreshImports();
      queueVisible.value = true;
    }
    if (paywalledResults.length > 0) {
      await ElMessageBox.alert(paywalledListHtml(paywalledResults), "付费墙文献 · 需手动下载", {
        dangerouslyUseHTMLString: true,
        confirmButtonText: "知道了",
      }).catch(() => undefined); // 用户关闭弹窗不算失败
    }
    selected.value = new Set();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "提交失败");
  }
}

let polling = false;
async function refreshImports() {
  if (polling) return;
  polling = true;
  try {
    imports.value = await listImports();
  } catch {
    /* 轮询失败静默, 下次重试 */
  } finally {
    polling = false;
  }
}

async function handleRetry(importId: number) {
  try {
    await retryImport(importId);
    ElMessage.success("已重新排队");
    await refreshImports();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "重试失败");
  }
}

async function openVpnLogin() {
  try {
    const status = await browserLogin();
    browserStatus.value = status;
    vpnDialogVisible.value = false;
    ElMessage.success(status.message || "VPN 登录流程已启动");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "VPN 登录失败");
  }
}

onMounted(async () => {
  try {
    browserStatus.value = await getBrowserStatus();
  } catch {
    /* 获取浏览器状态失败时保持默认值, 不阻塞后续初始化 */
  }
  if (disposed) return; // 卸载后不再启动轮询, 避免孤儿定时器
  // VPN 登录弹窗暂不自动触发: L3 浏览器下载默认停用(未来启用时恢复)
  pollTimer = window.setInterval(refreshImports, 3000);
  refreshImports();
});

onBeforeUnmount(() => {
  disposed = true;
  abortCtrl?.abort();
  if (pollTimer) window.clearInterval(pollTimer);
});
</script>

<template>
  <div class="research-panel">
    <div class="search-row">
      <el-input
        v-model="query"
        placeholder="输入科研需求，如：轻量级图像超分辨率的注意力机制"
        clearable
        @keyup.enter="doSearch"
      />
      <el-select v-model="yearMin" placeholder="起始年" clearable class="year-select" @change="onFilterChange">
        <el-option v-for="y in yearOptions" :key="'min-' + y" :label="y" :value="y" />
      </el-select>
      <el-select v-model="yearMax" placeholder="结束年" clearable class="year-select" @change="onFilterChange">
        <el-option v-for="y in yearOptions" :key="'max-' + y" :label="y" :value="y" />
      </el-select>
      <el-button type="primary" :loading="searching" @click="doSearch">
        <el-icon><Search /></el-icon> 搜索
      </el-button>
      <!-- 年份筛选为前端实时过滤, 需重新按年份做后端真过滤时手动触发 -->
      <el-button :disabled="searching || paging" @click="doSearch">
        <el-icon><Refresh /></el-icon> 搜索新结果
      </el-button>
    </div>

    <div class="process-strip" v-if="processLines.length">
      <div v-for="(line, i) in processLines" :key="i" class="process-line">· {{ line }}</div>
    </div>

    <div v-if="summaryLine" class="filter-summary">
      <span>{{ summaryLine }}</span>
      <el-tag v-if="filterActive" size="small" class="filter-tag">
        年份 {{ yearMin ?? "不限" }} – {{ yearMax ?? "不限" }}
      </el-tag>
    </div>

    <div class="result-list" v-loading="searching || paging">
      <article v-for="(r, i) in filteredResults" :key="normKey(r)" class="result-card" :class="{ picked: selected.has(i) }">
        <label class="pick">
          <input type="checkbox" :checked="selected.has(i)" @change="toggleSelect(i)" />
        </label>
        <div class="result-main">
          <h3>{{ r.title }}</h3>
          <p class="result-authors">{{ r.authors.join(" · ") || "作者未知" }}</p>
          <p class="result-abstract">{{ r.abstract }}</p>
          <div class="result-meta">
            <span>{{ r.year || "—" }}</span>
            <el-tag size="small" class="source-tag">{{ sourceLabel(r.source) }}</el-tag>
            <!-- 发表状态以 published 为唯一事实源, venue 仅作后缀, 避免空 venue 渲染空标签 -->
            <el-tag v-if="r.published" size="small" type="info">已发表{{ r.venue ? " · " + r.venue : "" }}</el-tag>
            <el-tag v-else size="small" class="preprint-tag">预印本</el-tag>
            <el-tag v-if="r.ccf_level" size="small" class="ccf-tag" :class="'ccf-' + r.ccf_level.toLowerCase()">
              CCF-{{ r.ccf_level }}
            </el-tag>
            <span v-if="r.citations">被引 {{ r.citations }}</span>
            <el-tag v-if="r.oa_status === 'open'" size="small" type="success">开放获取</el-tag>
            <el-tag v-else-if="r.oa_status === 'unknown' && r.pdf_url" size="small" type="success">开放获取</el-tag>
            <el-tag v-else-if="r.oa_status === 'closed'" size="small" type="warning">付费墙·需手动下载</el-tag>
            <el-tag v-else size="small" type="warning">付费墙·需手动下载</el-tag>
            <span v-if="r.citations > 0" class="cite-count">被引 {{ r.citations }} 次</span>
            <a v-if="r.page_url" :href="r.page_url" target="_blank" rel="noopener">页面链接</a>
          </div>
        </div>
      </article>
      <el-empty v-if="emptyState" :description="emptyState.text">
        <el-button v-if="emptyState.outOfBounds" size="small" @click="goBackPage">返回上一页</el-button>
      </el-empty>
    </div>

    <!-- 分页栏可见性基于 hasSearched + loadedTotal, 与 results.length 解耦: 越界/空页不会让分页栏消失 -->
    <div v-if="hasSearched && loadedTotal > 0" class="pager-bar">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="PAGE_SIZE_OPTIONS"
        :total="loadedTotal"
        :pager-count="7"
        layout="sizes, prev, pager, next, total"
        :disabled="searching || paging"
        @size-change="onSizeChange"
        @current-change="onPageChange"
      />
      <span v-if="!hasMore" class="pager-hint">已加载全部 {{ loadedTotal }} 条</span>
    </div>

    <div class="action-bar" v-if="filteredResults.length">
      <span>已选 {{ selected.size }} 篇</span>
      <el-button type="primary" :disabled="selected.size === 0" @click="downloadSelected">
        <el-icon><Download /></el-icon> 下载并入库
      </el-button>
      <el-button @click="queueVisible = true">导入队列</el-button>
    </div>

    <ImportQueueDrawer v-model="queueVisible" :imports="imports" @retry="handleRetry" />

    <el-dialog v-model="vpnDialogVisible" title="登录西南交通大学 VPN" width="440px">
      <p>下载付费墙论文需要学校 VPN 会话。将在本机打开浏览器，请在弹出的窗口中完成 VPN 登录（仅首次需要）。</p>
      <template #footer>
        <el-button @click="vpnDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="openVpnLogin"><el-icon><SwitchButton /></el-icon> 打开浏览器登录</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.research-panel { padding: 8px 0 30px; }
.search-row { display: flex; gap: 12px; margin-bottom: 14px; }
.search-row :deep(.el-input) { flex: 1; }
.search-row :deep(.el-select) { width: 108px; flex: none; }
.search-row :deep(.el-button--primary) { background: #b66042; border-color: #b66042; }
.process-strip { margin-bottom: 14px; padding: 10px 14px; border-left: 3px solid #193944; background: rgba(255,255,255,.55); color: #51646a; font-size: 12px; }
.process-line { line-height: 1.9; }
.filter-summary { display: flex; align-items: center; gap: 10px; margin: -4px 0 10px; color: #51646a; font: 12px ui-monospace, monospace; }
.filter-summary :deep(.filter-tag) { color: #51646a; background: #eef2ef; border-color: #d3dcd6; }
.result-list { display: grid; gap: 12px; min-height: 160px; }
.result-card { display: flex; gap: 12px; padding: 16px 18px; border: 1px solid #d5d0c4; background: rgba(255,253,247,.86); transition: .18s; }
.result-card.picked { border-color: #b66042; background: #fff6ec; }
.pick input { width: 16px; height: 16px; accent-color: #b66042; }
.result-main { flex: 1; min-width: 0; }
.result-card h3 { margin: 0 0 6px; font: 17px/1.45 Georgia, "Noto Serif SC", serif; font-weight: 600; color: #1d333c; }
.result-authors { color: #b66042; font: 11px Georgia, serif; }
.result-abstract { margin: 10px 0; color: #69777a; font-size: 12px; line-height: 1.7; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.result-meta { display: flex; gap: 10px; align-items: center; color: #929a98; font: 10px ui-monospace, monospace; flex-wrap: wrap; }
.result-meta .cite-count { color: #b66042; font-weight: 600; }
.result-meta a { color: #193944; text-decoration: underline; }
/* 预印本: 中性灰标签 */
.result-meta :deep(.preprint-tag) { color: #929a98; background: #f1f0ec; border-color: #dddcd4; }
/* 来源小标: 中性浅绿灰 */
.result-meta :deep(.source-tag) { color: #51646a; background: #eef2ef; border-color: #d3dcd6; }
/* CCF 徽标: A 金色 / B 蓝色 / C 灰色 */
.result-meta :deep(.ccf-tag.ccf-a) { color: #8a6410; background: #f7ecc8; border-color: #dfc47a; }
.result-meta :deep(.ccf-tag.ccf-b) { color: #1d4f91; background: #dbe9f8; border-color: #a9c8ec; }
.result-meta :deep(.ccf-tag.ccf-c) { color: #6b7075; background: #e9ebed; border-color: #c9cdd2; }
/* 页码导航 */
.pager-bar { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 18px 0 6px; }
.pager-bar :deep(.el-pagination) { --el-pagination-button-bg-color: rgba(255,253,247,.7); --el-pagination-hover-color: #b66042; }
.pager-hint { color: #929a98; font: 11px ui-monospace, monospace; }
.action-bar { position: sticky; bottom: 0; display: flex; gap: 12px; align-items: center; margin-top: 18px; padding: 12px 16px; background: rgba(241,238,229,.92); backdrop-filter: blur(6px); border-top: 1px solid #d5d0c4; }
.action-bar span { color: #51646a; font-size: 12px; margin-right: auto; }
.action-bar :deep(.el-button--primary) { background: #b66042; border-color: #b66042; }
</style>
