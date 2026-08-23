<script setup lang="ts">
import { computed } from "vue";
import "katex/dist/katex.min.css";

import { markdownToHtml, parseScientificText, renderScientificMath } from "@/utils/scientificText";

const props = defineProps<{ content: string }>();

const segments = computed(() => {
  const parsed = parseScientificText(props.content);
  return parsed.map((segment, index) => {
    if (segment.kind === "math") {
      return { ...segment, html: renderScientificMath(segment.content, segment.display) };
    }
    const previous = parsed[index - 1];
    const next = parsed[index + 1];
    // 显示公式边界处的空白(空格/换行)在 pre-wrap 下会渲染成空行盒子,全部去掉,
    // 由公式自身 margin 提供与前后文的间距(更接近期刊排版)
    if ((previous?.kind === "math" && previous.display) || (next?.kind === "math" && next.display)) {
      const content = segment.content.replace(/^\s+/, "").replace(/\s+$/, "");
      if (content !== segment.content) return { ...segment, content };
    }
    return segment;
  });
});
</script>

<template>
  <div class="scientific-text">
    <template v-for="(segment, index) in segments" :key="index">
      <span v-if="segment.kind === 'text'" class="scientific-text__prose" v-html="markdownToHtml(segment.content)" />

      <span v-else-if="!segment.display" class="scientific-text__inline-math">
        <span v-if="segment.html" v-html="segment.html" />
        <code v-else>{{ segment.content }}</code>
      </span>

      <span v-else class="scientific-text__equation">
        <span class="scientific-text__equation-scroll">
          <span v-if="segment.html" v-html="segment.html" />
          <code v-else>{{ segment.content }}</code>
        </span>
        <span v-if="segment.equationNumber" class="scientific-text__equation-number">{{ segment.equationNumber }}</span>
      </span>
    </template>
  </div>
</template>

<style scoped>
.scientific-text {
  color: #344b52;
  font: 400 14px/1.95 "Noto Serif SC", Georgia, serif;
}

.scientific-text__prose { white-space: pre-wrap; }
.scientific-text__prose :deep(h1), .scientific-text__prose :deep(h2), .scientific-text__prose :deep(h3) {
  margin: 14px 0 8px;
  color: #183641;
  font-weight: 600;
  line-height: 1.4;
}
.scientific-text__prose :deep(h1) { font-size: 18px; }
.scientific-text__prose :deep(h2) { font-size: 16px; }
.scientific-text__prose :deep(h3) { font-size: 15px; }
.scientific-text__prose :deep(ul), .scientific-text__prose :deep(ol) { margin: 6px 0 6px 1.2em; padding: 0; }
.scientific-text__prose :deep(li) { margin: 3px 0; }
.scientific-text__prose :deep(code) { padding: 1px 5px; border-radius: 3px; background: #f1ece2; color: #53676d; font: 12px/1.5 Consolas, monospace; }
.scientific-text__prose :deep(strong) { color: #1e3942; }

.scientific-text__inline-math {
  display: inline;
  margin: 0 .12em;
}

.scientific-text__equation {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px;
  margin: 8px 0;
}

.scientific-text__equation-scroll {
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
  padding: 2px 0;
  text-align: center;
  scrollbar-width: none;
  -ms-overflow-style: none;
}
.scientific-text__equation-scroll::-webkit-scrollbar { display: none; }

.scientific-text__equation-number {
  padding-left: 14px;
  color: #7f8582;
  font: 12px/1.2 Georgia, serif;
  text-align: right;
}

.scientific-text code {
  padding: 1px 5px;
  border-radius: 3px;
  background: #f1ece2;
  color: #53676d;
  font: 12px/1.5 Consolas, monospace;
  white-space: pre-wrap;
}

.scientific-text :deep(.katex) { font-size: 1.05em; }
.scientific-text__equation-scroll :deep(.katex-display) {
  width: max-content;
  margin: 0 auto;
}

@media (max-width: 720px) {
  .scientific-text__equation { margin: 6px 0; }
}
</style>
