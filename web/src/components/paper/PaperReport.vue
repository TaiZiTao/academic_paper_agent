<script setup lang="ts">
import { computed } from "vue";
import { Download } from "@element-plus/icons-vue";
import type { PaperArtifact, PaperCitation, PaperFigure } from "@/types/paper";
import PaperCitationList from "./PaperCitationList.vue";
import ScientificText from "./ScientificText.vue";

const props = defineProps<{ artifact?: PaperArtifact; figures?: PaperFigure[] }>();
const emit = defineEmits<{
  openCitation: [citation: PaperCitation];
  openPage: [page: number];
}>();

const sections = computed(() => {
  const content = props.artifact?.content || {};
  const definitions = [
    ["background", "01", "研究背景与方向"],
    ["motivation", "02", "论文动机"],
    ["existing_problems", "03", "现有方法存在的问题"],
    ["solution", "04", "解决方案与创新点"],
    ["contributions", "05", "论文主要贡献"],
  ] as const;
  return definitions.map(([key, number, label]) => ({ key, number, label, value: String(content[key] || "原文未提供充分证据") }));
});

const terms = computed(() => {
  const raw = props.artifact?.content?.terms;
  return Array.isArray(raw) ? raw as Array<Record<string, string>> : [];
});

function captionText(caption: string, kind: string): string {
  const pattern = kind === "table" ? /^(?:TABLE|Table|表)\s*/i : /^(?:图|Fig(?:ure)?\.?)\s*/i;
  return caption.replace(pattern, "");
}

function exportMarkdown() {
  if (!props.artifact) return;
  const lines: string[] = [`# ${props.artifact.title}`, "", props.artifact.content_text || "", ""];
  for (const section of sections.value) {
    lines.push(`## ${section.number} ${section.label}`, "", section.value, "");
  }
  if (terms.value.length) {
    lines.push("## 关键术语 / Terminology", "");
    for (const term of terms.value) {
      lines.push(`- **${term.en || term.term || ""}**: ${term.zh || term.translation || ""}`);
    }
    lines.push("");
  }
  if (props.artifact.citations?.length) {
    lines.push("## 引用来源", "");
    for (const citation of props.artifact.citations) {
      lines.push(`- p.${citation.page ?? "?"} ${citation.quote || ""}`);
    }
  }
  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "deep-reading-report.md";
  anchor.click();
  URL.revokeObjectURL(url);
}
</script>

<template>
  <div v-if="artifact" class="report">
    <header class="report-title">
      <span>DEEP READING / 深度精读</span>
      <div class="report-head">
        <h2>{{ artifact.title }}</h2>
        <button type="button" class="report-export" @click="exportMarkdown"><el-icon><Download /></el-icon>导出 Markdown</button>
      </div>
      <p>由论文原文证据生成；引用状态以证据索引中的“已验证/待核验”标记为准。</p>
    </header>

    <article v-for="section in sections" :key="section.key" class="report-section">
      <div class="section-number">{{ section.number }}</div>
      <div>
        <h3>{{ section.label }}</h3>
        <ScientificText :content="section.value" />
      </div>
    </article>

    <article v-if="terms.length" class="term-section">
      <h3>关键术语 / Terminology</h3>
      <div class="term-grid">
        <div v-for="(term, index) in terms" :key="index">
          <strong>{{ term.en || term.term }}</strong>
          <ScientificText :content="term.zh || term.translation || ''" />
        </div>
      </div>
    </article>

    <article v-if="figures?.length" class="report-figures">
      <h3>论文图表 / Figures & Tables</h3>
      <div class="figure-grid">
        <figure v-for="figure in figures" :key="figure.id" @click="emit('openPage', figure.page)">
          <img :src="figure.image_url" :alt="figure.caption" loading="lazy" />
          <figcaption>
            <span class="fig-kind">{{ figure.kind === "table" ? "TABLE" : "FIG" }}</span>
            <span>{{ captionText(figure.caption_translated || figure.caption, figure.kind) }}</span>
          </figcaption>
        </figure>
      </div>
    </article>

    <PaperCitationList :citations="artifact.citations" @open="emit('openCitation', $event)" />
  </div>
  <el-empty v-else description="精读报告正在生成" />
</template>

<style scoped>
.report { max-width: 820px; margin: 0 auto; }
.report-title { padding-bottom: 24px; border-bottom: 2px solid #183641; }
.report-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 8px; }
.report-export { display: inline-flex; align-items: center; gap: 4px; padding: 5px 12px; border: 1px solid #d3cbbd; border-radius: 5px; background: #faf6ee; color: #4f6166; font-size: 12px; cursor: pointer; flex: 0 0 auto; }
.report-export:hover { border-color: #b75f40; color: #b75f40; }
.report-title span { color: #b86243; font-size: 10px; letter-spacing: .2em; font-weight: 700; }
.report-title h2 { margin: 9px 0 6px; color: #183641; font-family: "Noto Serif SC", "Source Han Serif SC", Georgia, serif; font-size: 28px; font-weight: 600; }
.report-title p { color: #7b8585; font-size: 12px; }
.report-section { display: grid; grid-template-columns: 42px 1fr; gap: 14px; padding: 24px 0; border-bottom: 1px solid #e0dbce; }
.section-number { color: #c16f50; font: 12px/1.6 Georgia, serif; }
.report-section h3, .term-section h3 { margin: 0 0 9px; color: #1e3942; font-family: "Noto Serif SC", Georgia, serif; font-size: 16px; }
.report-section p { color: #3f5054; font-size: 14px; line-height: 1.9; white-space: pre-wrap; }
.term-section { padding: 24px 0; }
.term-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px; }
.term-grid div { display: grid; gap: 3px; padding: 11px; background: #ebe7dc; border-radius: 5px; }
.term-grid strong { color: #203941; font: 13px Georgia, serif; }
.term-grid span { color: #6e7776; font-size: 12px; }
.report-figures { padding: 24px 0; }
.report-figures h3 { margin: 0 0 14px; color: #1e3942; font-family: "Noto Serif SC", Georgia, serif; font-size: 16px; }
.figure-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 14px; }
.figure-grid figure { margin: 0; padding: 10px; border: 1px solid #e0dbce; background: rgba(255,255,255,.62); border-radius: 6px; cursor: pointer; }
.figure-grid figure:hover { border-color: #b75f40; }
.figure-grid img { display: block; width: 100%; border-radius: 4px; }
.figure-grid figcaption { display: grid; gap: 3px; margin-top: 8px; }
.fig-kind { color: #b45f42; font: 700 9px/1.4 Georgia, serif; letter-spacing: .12em; }
.figure-grid figcaption span:last-child { color: #4a5b60; font: 400 12px/1.6 "Noto Serif SC", Georgia, serif; }
</style>
