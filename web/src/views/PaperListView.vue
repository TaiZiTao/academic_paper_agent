<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { Collection, Delete, DocumentAdd, RefreshRight, Search } from "@element-plus/icons-vue";
import { deletePaper, listPapers, listenPaperProgress, retryPaper, updatePaperField, uploadPaper } from "@/api/paper";
import type { PaperStatus, PaperSummary } from "@/types/paper";
import ResearchSearchPanel from "@/components/research/ResearchSearchPanel.vue";

const router = useRouter();
// 当前页签: library=论文档案 | search=文献检索
const activeTab = ref<"library" | "search">("library");
// 平铺列表: papers; 方向筛选: activeField + fields(方向清单)
const papers = ref<PaperSummary[]>([]);
const fields = ref<string[]>([]);
const total = ref(0);
const loading = ref(false);
const uploading = ref(false);
const dragActive = ref(false);
const search = ref("");
const activeField = ref("");
const fileInput = ref<HTMLInputElement>();
const progressText = ref<Record<number, string>>({});
const listeners = new Map<number, () => void>();
// 改方向弹窗
const editTarget = ref<PaperSummary | null>(null);
const editField = ref("");
const editing = ref(false);

const statusMeta: Record<PaperStatus, { label: string; tone: string }> = {
  uploaded: { label: "已上传", tone: "neutral" },
  parsing: { label: "解析文本", tone: "working" },
  indexing: { label: "建立索引", tone: "working" },
  reporting: { label: "生成报告", tone: "working" },
  ready: { label: "可精读", tone: "ready" },
  failed: { label: "处理失败", tone: "failed" },
};

// 请求序号: 快速切换筛选时丢弃过期响应, 避免旧请求覆盖新状态(UI 与数据不一致)
let fetchSeq = 0;
async function fetchPapers() {
  const seq = ++fetchSeq;
  loading.value = true;
  try {
    const data = await listPapers({
      search: search.value,
      page_size: 100,
      field: activeField.value,
    });
    if (seq !== fetchSeq) return; // 已有更新的请求, 丢弃本次结果
    if (!data) throw new Error("论文列表响应格式异常，请确认前后端服务已正确连接");
    if (!Array.isArray(data.items)) throw new Error("论文列表响应格式异常");
    papers.value = data.items;
    fields.value = data.fields || [];
    total.value = Number(data.total) || 0;
  } catch (error) {
    if (seq !== fetchSeq) return;
    papers.value = [];
    total.value = 0;
    ElMessage.error(error instanceof Error ? error.message : "论文列表加载失败");
  } finally {
    if (seq === fetchSeq) loading.value = false;
  }
}

function selectField(field: string) {
  activeField.value = activeField.value === field ? "" : field;
  // 点"全部"= 回到完整库: 同时清空搜索词, 避免残留搜索词继续过滤
  if (field === "") search.value = "";
  fetchPapers();
}

function openEditField(paper: PaperSummary) {
  editTarget.value = paper;
  editField.value = paper.research_field || "";
  editing.value = true;
}

async function saveField() {
  if (!editTarget.value) return;
  const field = editField.value.trim();
  try {
    await updatePaperField(editTarget.value.id, field);
    ElMessage.success("研究方向已更新");
    editing.value = false;
    editTarget.value = null;
    await fetchPapers();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "更新失败");
  }
}

function watchProgress(paperId: number) {
  listeners.get(paperId)?.();
  listeners.delete(paperId);
  const close = listenPaperProgress(
    paperId,
    async (event) => {
      progressText.value[paperId] = event.message || statusMeta[event.status]?.label || event.stage;
      const item = papers.value.find((paper) => paper.id === paperId);
      if (item && event.status) item.status = event.status;
      if (event.event === "done" || event.event === "error") {
        listeners.delete(paperId);
        await fetchPapers();
      }
    },
    () => fetchPapers(),
  );
  listeners.set(paperId, close);
}

async function acceptFile(file?: File) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    ElMessage.warning("请选择 PDF 论文文件");
    return;
  }
  uploading.value = true;
  try {
    const paper = await uploadPaper(file);
    papers.value.unshift(paper);
    progressText.value[paper.id] = "PDF 已保存，准备解析";
    watchProgress(paper.id);
    ElMessage.success("论文已进入精读流程");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "上传失败");
  } finally {
    uploading.value = false;
    if (fileInput.value) fileInput.value.value = "";
  }
}

function delayStyle(index: number) {
  return { "--delay": `${index * 45}ms` };
}

function onDrop(event: DragEvent) {
  dragActive.value = false;
  acceptFile(event.dataTransfer?.files[0]);
}

async function handleRetry(paper: PaperSummary) {
  await retryPaper(paper.id);
  paper.status = "uploaded";
  watchProgress(paper.id);
}

async function handleDelete(paper: PaperSummary) {
  await ElMessageBox.confirm(`删除《${paper.title}》及其报告、问答和索引？`, "删除论文", { type: "warning" });
  await deletePaper(paper.id);
  listeners.get(paper.id)?.();
  listeners.delete(paper.id);
  papers.value = papers.value.filter((item) => item.id !== paper.id);
  total.value -= 1;
  ElMessage.success("论文及相关数据已清理");
}

onMounted(fetchPapers);
onBeforeUnmount(() => listeners.forEach((close) => close()));
</script>

<template>
  <div class="paper-library">
    <header class="hero-band">
      <div class="hero-copy">
        <span class="eyebrow">PAPER LIBRARY</span>
        <h1>论文库</h1>
        <p>上传论文，自动解析全文、识别图表并生成精读报告</p>
      </div>
      <div class="hero-count">
        <strong>{{ total }}</strong>
        <span>篇论文已入库</span>
      </div>
    </header>

    <div class="library-tabs">
      <button type="button" :class="{ active: activeTab === 'library' }" @click="activeTab = 'library'">论文档案</button>
      <button type="button" :class="{ active: activeTab === 'search' }" @click="activeTab = 'search'">文献检索</button>
    </div>

    <div v-show="activeTab === 'library'">
      <section
        class="drop-zone"
        :class="{ active: dragActive }"
        @dragover.prevent="dragActive = true"
        @dragleave.prevent="dragActive = false"
        @drop.prevent="onDrop"
      >
        <div class="drop-mark"><el-icon><DocumentAdd /></el-icon></div>
        <div>
          <strong>拖入 PDF 论文，或点击按钮上传</strong>
          <p>自动解析全文、识别图表并生成精读报告</p>
        </div>
        <el-button type="primary" :loading="uploading" @click="fileInput?.click()">上传论文</el-button>
        <input
          ref="fileInput"
          type="file"
          accept="application/pdf"
          hidden
          @change="acceptFile(($event.target as HTMLInputElement).files?.[0])"
        />
      </section>

      <section class="library-toolbar">
        <div><span>LIBRARY INDEX</span><h2>论文档案</h2></div>
        <div class="toolbar-right">
          <el-input v-model="search" clearable placeholder="按标题、摘要或作者查找" @keyup.enter="fetchPapers">
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </div>
      </section>

      <section class="field-bar">
        <button type="button" :class="{ active: activeField === '' }" @click="selectField('')">全部</button>
        <button v-for="f in fields" :key="f" type="button" :class="{ active: activeField === f }" @click="selectField(f)">{{ f }}</button>
      </section>

      <div v-loading="loading" class="paper-grid">
        <article v-for="(paper, index) in papers" :key="paper.id" class="paper-card" :style="delayStyle(index)">
          <button class="card-main" type="button" :disabled="paper.status !== 'ready'" @click="router.push(`/papers/${paper.id}`)">
            <div class="card-topline">
              <span :class="['status-pill', statusMeta[paper.status].tone]"><i />{{ statusMeta[paper.status].label }}</span>
              <span class="paper-id">NO. {{ String(paper.id).padStart(3, "0") }}</span>
            </div>
            <h3>{{ paper.title || paper.original_filename }}</h3>
            <p class="paper-authors">{{ paper.authors.join(" · ") || "作者信息未识别" }}</p>
            <p class="paper-abstract">{{ paper.abstract || progressText[paper.id] || "等待生成论文摘要与精读报告。" }}</p>
            <div class="paper-meta"><span>{{ paper.language.toUpperCase() }}</span><span>{{ paper.page_count || "—" }} PAGES</span><span>{{ paper.original_filename }}</span></div>
          </button>
          <footer>
            <span v-if="paper.status === 'failed'" class="error-text">{{ paper.error_code === "unsupported_scan" ? "扫描版 PDF 暂不支持" : paper.error_message || "处理失败" }}</span>
            <span v-else>{{ progressText[paper.id] || (paper.status === "ready" ? "打开精读工作台 →" : "处理中，请稍候") }}</span>
            <div class="card-actions">
              <el-button text circle aria-label="改方向" @click="openEditField(paper)"><el-icon><Collection /></el-icon></el-button>
              <el-button v-if="paper.status === 'failed'" text circle aria-label="重试" @click="handleRetry(paper)"><el-icon><RefreshRight /></el-icon></el-button>
              <el-button text circle aria-label="删除" @click="handleDelete(paper)"><el-icon><Delete /></el-icon></el-button>
            </div>
          </footer>
        </article>
        <el-empty v-if="!loading && papers.length === 0" description="还没有论文，上传第一篇开始精读" />
      </div>
    </div>

    <ResearchSearchPanel v-if="activeTab === 'search'" />

    <!-- 研究方向编辑弹窗 -->
    <el-dialog v-model="editing" title="修改研究方向" width="420px">
      <el-select v-model="editField" filterable allow-create default-first-option placeholder="选择或输入研究方向" style="width: 100%">
        <el-option v-for="f in fields" :key="f" :label="f" :value="f" />
      </el-select>
      <template #footer>
        <el-button @click="editing = false">取消</el-button>
        <el-button type="primary" @click="saveField">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.paper-library { min-height: 100%; padding: 30px clamp(22px, 4vw, 58px) 56px; background: #f1eee5; color: #1d333c; background-image: linear-gradient(rgba(32,55,62,.035) 1px, transparent 1px), linear-gradient(90deg, rgba(32,55,62,.035) 1px, transparent 1px); background-size: 34px 34px; }
.hero-band { display: flex; justify-content: space-between; align-items: end; padding: 22px 0 28px; border-bottom: 2px solid #193944; }
.eyebrow, .library-toolbar span { color: #b66042; font-size: 10px; letter-spacing: .24em; font-weight: 700; }
.hero-copy h1 { margin: 7px 0; font-family: "Noto Serif SC", "Source Han Serif SC", Georgia, serif; font-size: clamp(34px, 5vw, 58px); line-height: 1; font-weight: 600; }
.hero-copy p { color: #64747a; font-size: 14px; }
.hero-count { display: grid; text-align: right; }
.hero-count strong { font: 38px/1 Georgia, serif; color: #b66042; }
.hero-count span { margin-top: 5px; color: #778488; font-size: 11px; }
.drop-zone { display: grid; grid-template-columns: auto 1fr auto; gap: 18px; align-items: center; margin: 26px 0 40px; padding: 22px 24px; border: 1px dashed #9ca9a7; background: rgba(255,255,255,.5); transition: .2s; }
.drop-zone.active { border-color: #b66042; background: #fff8ef; transform: translateY(-2px); }
.drop-mark { width: 46px; height: 46px; display: grid; place-items: center; background: #193944; color: #f2eee3; border-radius: 50%; font-size: 20px; }
.drop-zone strong { color: #263f48; font-family: Georgia, "Noto Serif SC", serif; font-size: 16px; }
.drop-zone p { margin-top: 4px; color: #7d898b; font-size: 11px; }
.drop-zone :deep(.el-button) { background: #b66042; border-color: #b66042; color: white; }
.library-toolbar { display: flex; justify-content: space-between; align-items: end; margin-bottom: 15px; }
.library-toolbar h2 { margin-top: 3px; font-family: Georgia, "Noto Serif SC", serif; font-size: 23px; font-weight: 500; }
.library-toolbar :deep(.el-input) { width: min(320px, 40vw); }
.paper-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; min-height: 220px; }
.paper-card { display: flex; flex-direction: column; min-height: 290px; border: 1px solid #d5d0c4; background: rgba(255,253,247,.84); box-shadow: 0 8px 28px rgba(35,51,53,.055); animation: rise .45s both; animation-delay: var(--delay); }
.card-main { flex: 1; padding: 20px 20px 16px; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.card-main:disabled { cursor: default; }
.card-topline { display: flex; justify-content: space-between; align-items: center; }
.status-pill { display: inline-flex; gap: 6px; align-items: center; font-size: 10px; letter-spacing: .08em; }
.status-pill i { width: 6px; height: 6px; border-radius: 50%; background: #8b9898; }
.status-pill.working i { background: #d79245; box-shadow: 0 0 0 4px rgba(215,146,69,.14); animation: pulse 1.3s infinite; }
.status-pill.ready i { background: #3d8e75; }
.status-pill.failed i { background: #bc513f; }
.paper-id { color: #a0a8a6; font: 9px ui-monospace, monospace; letter-spacing: .12em; }
.paper-card h3 { margin: 22px 0 8px; font-family: "Noto Serif SC", "Source Han Serif SC", Georgia, serif; font-size: 20px; line-height: 1.48; font-weight: 600; }
.paper-authors { color: #b66042; font: 11px Georgia, serif; }
.paper-abstract { display: -webkit-box; margin-top: 16px; overflow: hidden; color: #69777a; font-size: 12px; line-height: 1.7; -webkit-line-clamp: 3; -webkit-box-orient: vertical; }
.paper-meta { display: flex; gap: 10px; margin-top: 18px; color: #929a98; font: 9px ui-monospace, monospace; overflow: hidden; }
.toolbar-right { display: flex; gap: 12px; align-items: center; }
.field-bar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }
.field-bar button { padding: 5px 13px; border: 1px solid #c9c4b8; border-radius: 999px; background: rgba(255,255,255,.6); color: #51646a; font-size: 12px; cursor: pointer; transition: .18s; }
.field-bar button:hover { border-color: #b66042; color: #b66042; }
.field-bar button.active { background: #193944; border-color: #193944; color: #f2eee3; }
.paper-meta span:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.paper-card footer { min-height: 46px; display: flex; justify-content: space-between; align-items: center; padding: 8px 12px 8px 20px; border-top: 1px solid #e1ddd2; color: #748083; font-size: 10px; }
.error-text { color: #ad4f3c; }
.card-actions { display: flex; }
.library-tabs { display: flex; gap: 8px; margin: 20px 0 6px; }
.library-tabs button { padding: 7px 18px; border: 1px solid #c9c4b8; border-radius: 999px; background: rgba(255,255,255,.6); color: #51646a; font-size: 13px; cursor: pointer; transition: .18s; }
.library-tabs button:hover { border-color: #b66042; color: #b66042; }
.library-tabs button.active { background: #193944; border-color: #193944; color: #f2eee3; }
@keyframes rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
@keyframes pulse { 50% { opacity: .45; } }
@media (max-width: 720px) { .hero-count { display: none; } .drop-zone { grid-template-columns: auto 1fr; } .drop-zone > .el-button { grid-column: 1 / -1; } .library-toolbar { align-items: stretch; gap: 12px; flex-direction: column; } .library-toolbar :deep(.el-input) { width: 100%; } }
</style>