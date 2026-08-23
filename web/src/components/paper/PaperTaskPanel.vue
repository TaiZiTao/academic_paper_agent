<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Promotion } from "@element-plus/icons-vue";
import type {
  PaperArtifact,
  PaperCitation,
  PaperFigure,
  PaperMessage,
  PaperSection,
  PaperTaskType,
  PaperTranslationBlock,
} from "@/types/paper";
import PaperCitationList from "./PaperCitationList.vue";
import PaperSectionTree from "./PaperSectionTree.vue";
import ScientificText from "./ScientificText.vue";
import { stripVisualRegions } from "@/utils/paperVisualContent";

const props = defineProps<{
  taskType: PaperTaskType;
  sections: PaperSection[];
  messages: PaperMessage[];
  loading: boolean;
  streamContent: string;
  artifacts: PaperArtifact[];
  translationBlocks: PaperTranslationBlock[];
  figures: PaperFigure[];
  translationProgress: { current: number; total: number };
}>();
const emit = defineEmits<{
  submit: [payload: { inputText: string; section: string | null }];
  openCitation: [citation: PaperCitation];
  openPage: [page: number];
}>();

const inputText = ref("");
const selectedSection = ref<PaperSection | null>(null);
const presentationMinutes = ref(15);
watch(() => props.taskType, () => { inputText.value = ""; selectedSection.value = null; });

const orderedSections = computed(() => [...props.sections].sort((a, b) => a.ordinal - b.ordinal));

const parentTitles = computed(() => {
  const parents = new Set<string>();
  orderedSections.value.forEach((section, index) => {
    for (let cursor = index + 1; cursor < orderedSections.value.length; cursor += 1) {
      if (orderedSections.value[cursor].level <= section.level) break;
      parents.add(section.title);
    }
  });
  return parents;
});

// 选中章节的整章范围:自身 + 全部后代章节(与后端 work_sections 一致)
const chapterTitles = computed(() => {
  const titles = new Set<string>();
  const selected = selectedSection.value;
  if (!selected) return titles;
  const index = orderedSections.value.findIndex((item) => item.title === selected.title);
  if (index < 0) {
    titles.add(selected.title);
    return titles;
  }
  const level = orderedSections.value[index].level;
  titles.add(selected.title);
  for (let cursor = index + 1; cursor < orderedSections.value.length; cursor += 1) {
    const item = orderedSections.value[cursor];
    if (item.level <= level) break;
    titles.add(item.title);
  }
  return titles;
});

const isParentSection = computed(() => {
  const selected = selectedSection.value;
  return selected ? parentTitles.value.has(selected.title) : false;
});

// 整章范围内是否已有完整译文(用于按钮显示"重新翻译")
const fullyTranslated = computed(() => {
  const titles = chapterTitles.value;
  if (!titles.size) return false;
  const blocks = props.translationBlocks.filter((item) => item.section && titles.has(item.section));
  if (!blocks.length) return false;
  const scope = orderedSections.value.filter((item) => titles.has(item.title));
  const withBlocks = new Set(blocks.map((item) => item.section));
  return scope.every((item) => withBlocks.has(item.title));
});

const completedSections = computed(() => {
  const done = new Set<string>();
  // 1) 单独翻译的章节: 有 translation artifact
  props.artifacts
    .filter((item) => item.artifact_type === "translation")
    .map((item) => String(item.content.section || item.title.replace(/^章节翻译：/, "")))
    .filter(Boolean)
    .forEach((title) => done.add(title));
  // 2) 整章翻译覆盖的子章节: translation_blocks 表里有 completed 块
  //    (父章节翻译不建 artifact, 译文块落在各子章节的块记录上)
  props.translationBlocks
    .filter((item) => item.status === "completed" && item.section)
    .forEach((item) => done.add(item.section as string));
  // 父章节:全部直接子章节完成时视为完成
  orderedSections.value.forEach((section, index) => {
    const children: string[] = [];
    for (let cursor = index + 1; cursor < orderedSections.value.length; cursor += 1) {
      if (orderedSections.value[cursor].level <= section.level) break;
      if (orderedSections.value[cursor].level === section.level + 1) children.push(orderedSections.value[cursor].title);
    }
    if (children.length && children.every((title) => done.has(title))) done.add(section.title);
  });
  return [...done];
});

const visibleTranslationBlocks = computed<PaperTranslationBlock[]>(() => {
  if (!selectedSection.value) return [];
  const titles = chapterTitles.value;
  const order = new Map(orderedSections.value.map((item, index) => [item.title, index]));
  const rank = (block: PaperTranslationBlock) => (order.get(block.section || "") ?? 999) * 10000 + block.block_index;
  const live = props.translationBlocks.filter((item) => item.section && titles.has(item.section));
  if (live.length) return [...live].sort((a, b) => rank(a) - rank(b));
  const stored = props.artifacts.find(
    (item) => item.artifact_type === "translation" && item.content.section === selectedSection.value?.title,
  );
  if (Array.isArray(stored?.content.blocks)) {
    return (stored.content.blocks as unknown as PaperTranslationBlock[]).sort((a, b) => rank(a) - rank(b));
  }
  const partial = props.translationBlocks.filter((item) => item.section && titles.has(item.section));
  return [...partial].sort((a, b) => rank(a) - rank(b));
});

// 选中章节范围内的图表(按页码过滤 + 排序)
const visibleFigures = computed<PaperFigure[]>(() => {
  if (!selectedSection.value) return [];
  const scope = orderedSections.value.filter((item) => chapterTitles.value.has(item.title));
  if (!scope.length) return [];
  const minPage = Math.min(...scope.map((item) => item.page_start));
  const maxPage = Math.max(...scope.map((item) => item.page_end));
  const ownerTitles = new Set(scope.map((item) => item.title));
  return (props.figures || [])
    .filter((item) => item.page >= minPage && item.page <= maxPage)
    .filter((item) => {
      // 每个图表只归属一个章节,避免跨章节重复显示
      // 表格优先归属起始页等于其页码的章节(TABLE I 在第 6 页 -> IV. EXPERIMENTS)
      // 图则归属第一个覆盖其页码的章节(Fig. 2 -> A. Network Architecture)
      let owner = null;
      if (item.kind === "table") {
        owner = orderedSections.value.find((section) => section.page_start === item.page);
      }
      owner = owner || orderedSections.value.find(
        (section) => section.page_start <= item.page && item.page <= section.page_end,
      );
      return owner ? ownerTitles.has(owner.title) : true;
    })
    .sort((a, b) => a.page - b.page || a.ordinal - b.ordinal);
});

// 译文块 + 图表按页码合并(同页图表在前)
const visibleItems = computed(() => {
  const blocks = visibleTranslationBlocks.value.map((block) => ({
    type: "block" as const,
    page: block.page_start,
    key: "b-" + block.section + "-" + block.block_index,
    data: block,
  }));
  const figs = visibleFigures.value.map((figure) => ({
    type: "figure" as const,
    page: figure.page,
    key: "f-" + figure.id,
    data: figure,
  }));
  return [...blocks, ...figs].sort(
    (a, b) => a.page - b.page || (a.type === "figure" ? -1 : 1),
  );
});

const progressPercent = computed(() => props.translationProgress.total
  ? Math.round((props.translationProgress.current / props.translationProgress.total) * 100)
  : 0);

const meta = computed(() => ({
  qa: { title: "与论文对话", hint: "追问方法、实验、结论或局限，回答会携带原文页码。", button: "发送问题" },
  translation: { title: "章节翻译", hint: "选择一个章节，生成保留专业术语的中文译文。", button: "翻译章节" },
  presentation: { title: "汇报提纲", hint: "生成可直接用于组会或答辩的逐页提纲。", button: "生成提纲" },
  review: { title: "论文审稿", hint: "以审稿人视角评估论文的贡献、优缺点与修改建议。", button: "生成审稿意见" },
})[props.taskType]);

function submit() {
  let text = props.taskType === "translation" ? selectedSection.value?.title || "" : inputText.value.trim() || meta.value.title;
  if (props.taskType === "presentation") {
    text = `${presentationMinutes.value}分钟${inputText.value.trim() ? "：" + inputText.value.trim() : ""}`;
  }
  if (!text) return;
  emit("submit", { inputText: text, section: selectedSection.value?.title || null });
  if (props.taskType === "qa") inputText.value = "";
}

function selectSection(section: PaperSection) {
  selectedSection.value = section;
}

// 显示 caption 时去掉开头的 kind 字样(如 "TABLE"/"图"/"Fig."),由左侧标签承担
function captionText(caption: string, kind: string): string {
  const pattern = kind === "table" ? /^(?:TABLE|Table|表)\s*/i : /^(?:图|Fig(?:ure)?\.?)\s*/i;
  return caption.replace(pattern, "");
}
</script>

<template>
  <div class="task-panel">
    <header><span>ON-DEMAND WORKBENCH</span><h2>{{ meta.title }}</h2><p>{{ meta.hint }}</p></header>

    <div v-if="taskType === 'qa'" class="message-list">
      <div v-for="message in messages" :key="message.id" :class="['message', message.role]">
        <small>{{ message.role === "user" ? "YOU" : "PAPER AGENT" }}</small>
        <ScientificText v-if="message.role === 'assistant'" :content="message.content" />
        <p v-else>{{ message.content }}</p>
        <div v-if="message.role === 'assistant' && message.suggestions?.length" class="suggestion-chips">
          <button
            v-for="(suggestion, index) in message.suggestions"
            :key="index"
            type="button"
            @click="emit('submit', { inputText: suggestion, section: null })"
          >
            {{ suggestion }}
          </button>
        </div>
        <PaperCitationList
          v-if="message.role === 'assistant'"
          :citations="message.citations"
          @open="emit('openCitation', $event)"
        />
      </div>
      <div v-if="streamContent" class="message assistant streaming">
        <small>PAPER AGENT</small>
        <ScientificText :content="streamContent" />
      </div>
      <div v-else-if="loading" class="message assistant thinking">
        <small>PAPER AGENT</small>
        <span class="typing-hint">正在检索论文证据并生成回答…</span>
      </div>
    </div>

    <div v-if="streamContent && taskType !== 'translation' && taskType !== 'qa'" class="stream-card"><small>正在生成</small><ScientificText :content="streamContent" /></div>

    <div v-if="taskType === 'translation'" class="translation-workbench">
      <PaperSectionTree
        :sections="sections"
        :selected-section="selectedSection?.title || null"
        :completed-sections="completedSections"
        :running-section="loading ? selectedSection?.title || null : null"
        @select="selectSection"
        @open-page="emit('openPage', $event)"
      />

      <section class="translation-reader">
        <template v-if="selectedSection">
          <div class="translation-title">
            <div>
              <span>CHINESE TRANSLATION</span>
              <h3>{{ selectedSection.title }}</h3>
              <button type="button" @click="emit('openPage', selectedSection.page_start)">
                原文 p.{{ selectedSection.page_start }}<template v-if="selectedSection.page_end !== selectedSection.page_start">–{{ selectedSection.page_end }}</template>
              </button>
            </div>
            <el-button type="primary" :loading="loading" @click="submit">
              <el-icon><Promotion /></el-icon>
              {{ isParentSection ? (fullyTranslated ? "重新翻译整章" : "翻译整章") : (fullyTranslated ? "重新翻译本章" : "翻译本章") }}
            </el-button>
          </div>

          <div v-if="loading && translationProgress.total" class="translation-progress">
            <div><span>正在按原文顺序翻译</span><strong>{{ translationProgress.current }} / {{ translationProgress.total }}</strong></div>
            <div class="progress-track"><i :style="{ width: `${progressPercent}%` }" /></div>
          </div>

          <div v-if="visibleItems.length" class="translation-blocks">
            <template v-for="item in visibleItems" :key="item.key">
              <article v-if="item.type === 'block'" :class="['translation-block', item.data.status]">
                <button type="button" @click="emit('openPage', item.data.page_start)">
                  p.{{ item.data.page_start }}<template v-if="item.data.page_end !== item.data.page_start">–{{ item.data.page_end }}</template>
                </button>
                <ScientificText :content="stripVisualRegions(item.data.content)" />
                <small v-if="item.data.status === 'failed'">{{ item.data.error_message || "该段翻译失败，可点击继续翻译重试" }}</small>
              </article>
              <figure v-else class="translation-figure">
                <img :src="item.data.image_url" :alt="item.data.caption" loading="lazy" />
                <figcaption>
                  <div class="figure-caption-line">
                    <span class="figure-kind">{{ item.data.kind === "table" ? "TABLE" : "FIG" }}</span>
                    <p>{{ captionText(item.data.caption_translated || item.data.caption, item.data.kind) }}</p>
                  </div>
                  <button type="button" @click="emit('openPage', item.data.page)">原文 p.{{ item.data.page }}</button>
                </figcaption>
              </figure>
            </template>
          </div>
          <div v-else class="translation-empty">
            <strong>{{ isParentSection ? "准备翻译整章" : "准备翻译本章" }}</strong>
            <p>译文将按原文页码和段落顺序显示，默认仅展示中文。{{ isParentSection ? "再次点击会重新翻译整章并覆盖旧译文。" : "再次点击会重新翻译并覆盖旧译文。" }}</p>
          </div>
        </template>
        <div v-else class="translation-empty centered">
          <strong>从章节树选择一章</strong>
          <p>章节层级、页码范围和已翻译状态会保留在左侧。</p>
        </div>
      </section>
    </div>

    <div v-if="taskType !== 'translation'" class="task-form">
      <div v-if="taskType === 'presentation'" class="length-picker">
        <span>汇报篇幅</span>
        <button v-for="minutes in [5, 15, 30]" :key="minutes" type="button" :class="{ active: presentationMinutes === minutes }" @click="presentationMinutes = minutes">{{ minutes }} 分钟</button>
      </div>
      <el-input
        v-model="inputText"
        type="textarea"
        :rows="taskType === 'qa' ? 3 : 4"
        :placeholder="taskType === 'qa' ? '例如：作者的方法相比基线有什么改进？' : '可填写关注重点；留空则生成完整内容'"
        @keydown.ctrl.enter="submit"
      />
      <el-button type="primary" :loading="loading" @click="submit">
        <el-icon><Promotion /></el-icon>{{ meta.button }}
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.task-panel { max-width: 820px; margin: 0 auto; }
.task-panel:has(.translation-workbench) { max-width: 1120px; }
header { padding-bottom: 22px; border-bottom: 2px solid #183641; }
header span, .stream-card small, .message small { color: #b86243; font-size: 9px; letter-spacing: .18em; font-weight: 700; }
header h2 { margin: 8px 0 6px; color: #183641; font-family: "Noto Serif SC", Georgia, serif; font-size: 27px; }
header p { color: #748084; font-size: 12px; }
.message-list { display: grid; gap: 12px; margin: 22px 0; }
.message { max-width: 86%; padding: 13px 15px; border-radius: 4px 14px 14px 14px; background: white; border: 1px solid #e0dbcf; }
.message.user { justify-self: end; border-radius: 14px 4px 14px 14px; background: #173742; color: #eef1ed; border-color: #173742; }
.message p, .stream-card p { margin-top: 6px; line-height: 1.75; white-space: pre-wrap; }
.message :deep(.scientific-text), .stream-card :deep(.scientific-text) { margin-top: 6px; }
.message.user :deep(.scientific-text) { color: #eef1ed; }
.message.streaming { border-left: 2px solid #c16f50; animation: stream-in .18s ease both; }
.message.streaming::after { content: "▍"; color: #c16f50; animation: blink 1s steps(2) infinite; }
@keyframes stream-in { from { opacity: 0; transform: translateY(4px); } }
@keyframes blink { 50% { opacity: 0; } }
.message.thinking { color: #8a9698; }
.typing-hint::after { content: "…"; animation: blink 1s steps(2) infinite; }
.suggestion-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.suggestion-chips button {
  padding: 5px 11px;
  border: 1px solid #d8c7b8;
  border-radius: 999px;
  background: #fbf4ec;
  color: #a84f33;
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
  transition: background .15s ease, border-color .15s ease;
}
.suggestion-chips button:hover { background: #f3e0d0; border-color: #c16f50; }
.stream-card { margin: 20px 0; padding: 15px; border-left: 3px solid #c16f50; background: #fffdf7; }
.task-form { display: grid; gap: 12px; margin-top: 24px; padding: 18px; border: 1px solid #ddd7c9; background: rgba(255,255,255,.52); border-radius: 8px; }
.length-picker { display: flex; align-items: center; gap: 8px; }
.length-picker span { color: #748084; font-size: 12px; }
.length-picker button { padding: 4px 12px; border: 1px solid #d3cbbd; border-radius: 999px; background: #faf6ee; color: #4f6166; font-size: 12px; cursor: pointer; }
.length-picker button.active { background: #b75f40; border-color: #b75f40; color: #fff; }
.task-form :deep(.el-button--primary) { justify-self: end; background: #b85f40; border-color: #b85f40; }
.translation-workbench { display: grid; grid-template-columns: minmax(220px, 30%) minmax(0, 1fr); min-height: 520px; margin-top: 22px; }
.translation-reader { min-width: 0; padding: 2px 0 20px 28px; }
.translation-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; padding-bottom: 16px; border-bottom: 1px solid #d8d2c5; }
.translation-title span { color: #b45f42; font-size: 9px; font-weight: 700; letter-spacing: .18em; }
.translation-title h3 { margin: 7px 0 5px; color: #183641; font: 500 26px/1.3 Georgia, "Noto Serif SC", serif; }
.translation-title button, .translation-block > button { padding: 0; border: 0; background: none; color: #899497; font-size: 10px; cursor: pointer; }
.translation-title button:hover, .translation-block > button:hover { color: #b45f42; }
.translation-title :deep(.el-button--primary) { flex: 0 0 auto; background: #b85f40; border-color: #b85f40; }
.translation-progress { padding: 14px 0 2px; }
.translation-progress > div:first-child { display: flex; justify-content: space-between; color: #748084; font-size: 10px; }
.translation-progress strong { color: #b45f42; font-weight: 600; }
.progress-track { height: 3px; margin-top: 8px; overflow: hidden; background: #d9d3c8; }
.progress-track i { display: block; height: 100%; background: #b85f40; transition: width .25s ease; }
.translation-blocks { display: grid; }
.translation-block { position: relative; display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 13px; padding: 20px 0; border-bottom: 1px solid #ddd7ca; animation: translate-in .24s ease both; }
.translation-block > button { padding-top: 4px; color: #b45f42; text-align: left; }
.translation-block :deep(.scientific-text) { min-width: 0; }

.translation-figure {
  display: grid;
  gap: 10px;
  margin: 18px 0;
  padding: 16px;
  border: 1px solid #ddd7ca;
  background: rgba(255, 255, 255, .62);
  border-radius: 6px;
}
.translation-figure img {
  display: block;
  width: 100%;
  max-width: 640px;
  margin: 0 auto;
  border: 1px solid #e3ddd2;
  border-radius: 4px;
}
.translation-figure figcaption {
  display: grid;
  gap: 6px;
}
.figure-caption-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.figure-kind {
  flex: 0 0 auto;
  color: #b45f42;
  font: 700 9px/1.4 Georgia, serif;
  letter-spacing: .12em;
}
.figure-caption-line p {
  margin: 0;
  color: #344b52;
  font: 400 13px/1.7 "Noto Serif SC", Georgia, serif;
}
.translation-figure figcaption button {
  justify-self: start;
  padding: 0;
  border: 0;
  background: none;
  color: #899497;
  font-size: 10px;
  cursor: pointer;
}
.translation-figure figcaption button:hover { color: #b45f42; }
.translation-block small { grid-column: 2; color: #b34f42; }
.translation-block.failed { border-left: 2px solid #b34f42; padding-left: 10px; }
.translation-empty { margin-top: 30px; padding: 28px; border: 1px dashed #cfc8bb; color: #798589; text-align: center; background: rgba(255,255,255,.3); }
.translation-empty strong { color: #314b53; font: 500 17px Georgia, "Noto Serif SC", serif; }
.translation-empty p { margin: 8px 0 0; font-size: 12px; }
.translation-empty.centered { margin-top: 80px; }
@keyframes translate-in { from { opacity: 0; transform: translateY(5px); } }
@media (max-width: 820px) { .translation-workbench { grid-template-columns: 1fr; } .translation-reader { padding: 22px 0 0; } }
</style>
