<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { ArrowLeft, Document } from "@element-plus/icons-vue";
import { getPaperDetail, streamPaperTask } from "@/api/paper";
import PaperOutline from "@/components/paper/PaperOutline.vue";
import PaperPdfViewer from "@/components/paper/PaperPdfViewer.vue";
import PaperPresentation from "@/components/paper/PaperPresentation.vue";
import PaperReport from "@/components/paper/PaperReport.vue";
import PaperReview from "@/components/paper/PaperReview.vue";
import PaperTaskPanel from "@/components/paper/PaperTaskPanel.vue";
import type {
  PaperArtifact,
  PaperCitation,
  PaperDetail,
  PaperFigure,
  PaperMessage,
  PaperTaskType,
  PaperTranslationBlock,
} from "@/types/paper";

const route = useRoute();
const router = useRouter();
const paperId = Number(route.params.paperId);
const detail = ref<PaperDetail>();
const loading = ref(true);
const error = ref("");
const activeTab = ref<"report" | PaperTaskType>("report");
const pdfPage = ref(1);
const taskLoading = ref(false);
const streamContent = ref("");
const translationBlocks = ref<PaperTranslationBlock[]>([]);
const figures = ref<PaperFigure[]>([]);
const translationProgress = ref({ current: 0, total: 0 });
const mobilePdfOpen = ref(false);
const isNarrow = ref(window.innerWidth < 1180);
let controller: AbortController | undefined;

const sessionId = (() => {
  const key = `paper-session-${paperId}`;
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const created = `paper_${crypto.randomUUID()}`;
  localStorage.setItem(key, created);
  return created;
})();

const reportArtifact = computed(() => detail.value?.artifacts.find((item) => item.artifact_type === "report"));
const reviewArtifact = computed(() => detail.value?.artifacts.find((item) => item.artifact_type === "review"));
const taskArtifact = computed<PaperArtifact | undefined>(() => detail.value?.artifacts.find((item) => item.artifact_type === activeTab.value));
// 问答乐观消息:提交时立即显示用户问题, 生成完成后再由后端消息替换
const qaOptimistic = ref<PaperMessage[]>([]);
const messages = computed(() => [
  ...(detail.value?.messages || []).filter((item) => item.session_id === sessionId),
  ...qaOptimistic.value,
]);

const tabs: Array<{ key: "report" | PaperTaskType; label: string; index: string }> = [
  { key: "report", label: "精读报告", index: "01" },
  { key: "qa", label: "论文问答", index: "02" },
  { key: "translation", label: "章节翻译", index: "03" },
  { key: "presentation", label: "汇报提纲", index: "04" },
  { key: "review", label: "论文审稿", index: "05" },
];

async function loadDetail() {
  loading.value = true;
  try {
    detail.value = await getPaperDetail(paperId);
    figures.value = detail.value.figures || [];
    if (detail.value.paper.status !== "ready") error.value = "论文尚未完成处理，请稍后从论文列表重新打开。";
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "论文加载失败";
  } finally {
    loading.value = false;
  }
}

function openPage(page: number) {
  pdfPage.value = page;
  if (isNarrow.value) mobilePdfOpen.value = true;
}

function openCitation(citation: PaperCitation) {
  if (citation.page !== null) openPage(citation.page);
}


async function runTask(payload: { inputText: string; section: string | null }) {
  if (activeTab.value === "report") return;
  // 问答:立即插入用户问题气泡(乐观渲染), 生成完成后由后端消息替换
  if (activeTab.value === "qa" && payload.inputText.trim()) {
    qaOptimistic.value.push({
      id: -Date.now(),
      session_id: sessionId,
      role: "user",
      content: payload.inputText,
      citations: [],
      suggestions: [],
      created_at: new Date().toISOString(),
    });
  }
  controller?.abort();
  controller = new AbortController();
  taskLoading.value = true;
  streamContent.value = "";
  translationBlocks.value = [];
  translationProgress.value = { current: 0, total: 0 };
  try {
    await streamPaperTask(
      paperId,
      { task_type: activeTab.value, input_text: payload.inputText, session_id: sessionId, section: payload.section },
      {
        onToken: (token) => { streamContent.value += token; },
        onProgress: (data) => {
          if (activeTab.value === "translation") {
            translationProgress.value = {
              current: Number(data.current || 0),
              total: Number(data.total || 0),
            };
          }
        },
        onBlock: (block) => {
          const index = translationBlocks.value.findIndex(
            (item) => item.section === block.section && item.block_index === block.block_index,
          );
          if (index >= 0) translationBlocks.value[index] = block;
          else translationBlocks.value.push(block);
        },
        onFigure: (figure) => {
          const index = figures.value.findIndex((item) => item.id === figure.id);
          if (index >= 0) figures.value[index] = figure;
          else figures.value.push(figure);
        },
        onDone: async (done) => {
          await loadDetail();
          streamContent.value = "";
          const warnings = done?.warnings || [];
          if (activeTab.value === "translation" && warnings.length) {
            ElMessage.warning(`部分段落被过滤(可能是表格数据或图注), 译文可能不完整: ${warnings.join("; ")}`);
          }
        },
        onError: (message) => { throw new Error(message); },
      },
      controller.signal,
    );
  } catch (cause) {
    if ((cause as Error).name !== "AbortError") {
      await loadDetail();
      ElMessage.error(cause instanceof Error ? cause.message : "任务失败");
    }
  } finally {
    taskLoading.value = false;
    qaOptimistic.value = [];
  }
}

function handleResize() { isNarrow.value = window.innerWidth < 1180; }
onMounted(() => { loadDetail(); window.addEventListener("resize", handleResize); });
onBeforeUnmount(() => { controller?.abort(); window.removeEventListener("resize", handleResize); });
</script>

<template>
  <div v-loading="loading" :class="['workspace-page', { 'translation-mode': activeTab === 'translation' }]">
    <el-result v-if="error" icon="warning" title="暂时无法进入精读工作台" :sub-title="error">
      <template #extra><el-button @click="router.push('/papers')">返回论文列表</el-button></template>
    </el-result>

    <template v-else-if="detail">
      <PaperOutline v-if="activeTab !== 'translation'" :paper="detail.paper" :sections="detail.sections" :active-page="pdfPage" @open-page="openPage" />

      <main class="reading-desk">
        <header class="desk-header">
          <button type="button" @click="router.push('/papers')"><el-icon><ArrowLeft /></el-icon>论文库</button>
          <nav>
            <button v-for="tab in tabs" :key="tab.key" type="button" :class="{ active: activeTab === tab.key }" @click="activeTab = tab.key">
              <small>{{ tab.index }}</small>{{ tab.label }}
            </button>
          </nav>
          <button v-if="isNarrow" class="pdf-trigger" type="button" @click="mobilePdfOpen = true"><el-icon><Document /></el-icon>原文</button>
        </header>

        <section class="desk-content">
          <PaperReport v-if="activeTab === 'report'" :artifact="reportArtifact" :figures="figures" @open-citation="openCitation" @open-page="openPage" />
          <template v-else>
            <PaperTaskPanel
              :task-type="activeTab"
              :sections="detail.sections"
              :messages="messages"
              :loading="taskLoading"
              :stream-content="streamContent"
              :artifacts="detail.artifacts"
              :translation-blocks="translationBlocks.length ? translationBlocks : detail.translation_blocks"
              :figures="figures"
              :translation-progress="translationProgress"
              @submit="runTask"
              @open-citation="openCitation"
              @open-page="openPage"
            />
            <PaperReview v-if="activeTab === 'review'" :artifact="reviewArtifact" @open-citation="openCitation" />
            <PaperPresentation v-if="activeTab === 'presentation'" :artifact="taskArtifact" @open-citation="openCitation" />
          </template>
        </section>
      </main>

      <PaperPdfViewer v-if="!isNarrow" :paper-id="paperId" :page="pdfPage" :title="detail.paper.title" />
      <el-drawer v-model="mobilePdfOpen" title="论文原文" size="min(760px, 92vw)" append-to-body>
        <PaperPdfViewer :paper-id="paperId" :page="pdfPage" :title="detail.paper.title" />
      </el-drawer>
    </template>
  </div>
</template>

<style scoped>
.workspace-page { height: calc(100vh - var(--header-height)); display: grid; grid-template-columns: 260px minmax(440px, 1fr) minmax(380px, 40vw); overflow: hidden; background: #f3f0e7; }
.workspace-page.translation-mode { grid-template-columns: minmax(620px, 1fr) minmax(380px, 38vw); }
.reading-desk { min-width: 0; min-height: 0; height: 100%; display: flex; flex-direction: column; }
.desk-header { min-height: 68px; display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 20px; padding: 0 22px; border-bottom: 1px solid #d8d2c5; background: rgba(249,247,240,.94); }
.desk-header > button { display: inline-flex; align-items: center; gap: 5px; border: 0; background: none; color: #637277; font-size: 11px; cursor: pointer; }
.desk-header nav { display: flex; justify-content: center; gap: 4px; overflow-x: auto; }
.desk-header nav button { position: relative; display: flex; gap: 5px; align-items: baseline; padding: 22px 10px 19px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: #728085; white-space: nowrap; cursor: pointer; }
.desk-header nav button.active { border-bottom-color: #b75f40; color: #203b44; }
.desk-header nav small { color: #b6aaa0; font: 8px Georgia, serif; }
.pdf-trigger { color: #b75f40 !important; }
.desk-content { flex: 1; min-height: 0; padding: 34px clamp(22px, 4vw, 48px) 60px; overflow-y: auto; }

:deep(.el-drawer__body) { padding: 0; }
@media (max-width: 1180px) { .workspace-page { grid-template-columns: 240px minmax(0, 1fr); } .workspace-page.translation-mode { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 760px) { .workspace-page { grid-template-columns: 1fr; height: auto; min-height: calc(100vh - var(--header-height)); overflow: visible; } .workspace-page > :deep(.outline-panel) { display: none; } .reading-desk { min-height: calc(100vh - var(--header-height)); } .desk-header { position: sticky; top: 0; z-index: 5; padding: 0 10px; gap: 6px; } .desk-header nav { justify-content: flex-start; } .desk-header nav button { padding-inline: 7px; } .desk-header nav small { display: none; } .desk-content { padding: 24px 16px 48px; } }
</style>
