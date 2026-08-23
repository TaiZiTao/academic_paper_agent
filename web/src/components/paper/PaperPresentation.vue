<script setup lang="ts">
import { computed } from "vue";
import { Download } from "@element-plus/icons-vue";
import pptxgen from "pptxgenjs";
import type { PaperArtifact, PaperCitation } from "@/types/paper";
import PaperCitationList from "./PaperCitationList.vue";
import ScientificText from "./ScientificText.vue";

interface Slide {
  title: string;
  bullets: string[];
  notes: string;
}

const props = defineProps<{ artifact?: PaperArtifact }>();
const emit = defineEmits<{ openCitation: [citation: PaperCitation] }>();

const slides = computed<Slide[]>(() => {
  const raw = (props.artifact?.content?.slides || []) as Array<Record<string, unknown>>;
  return raw
    .map((item) => ({
      title: String(item.title || ""),
      bullets: Array.isArray(item.bullets) ? item.bullets.map(String) : [],
      notes: String(item.notes || ""),
    }))
    .filter((item) => item.title);
});

const markdown = computed(() => String(props.artifact?.content?.markdown || ""));

function downloadText(filename: string, text: string, mime: string) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function exportMarkdown() {
  if (!markdown.value) return;
  const header = `# ${props.artifact?.title || "论文汇报提纲"}\n\n`;
  downloadText("presentation-outline.md", header + markdown.value, "text/markdown;charset=utf-8");
}

async function exportPptx() {
  if (!slides.value.length) return;
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";
  for (const slide of slides.value) {
    const frame = pres.addSlide();
    frame.addText(slide.title, { x: 0.5, y: 0.35, w: 12, h: 0.7, fontSize: 26, bold: true, color: "183641" });
    slide.bullets.forEach((bullet, index) => {
      frame.addText(bullet, {
        x: 0.7, y: 1.25 + index * 0.55, w: 11.6, h: 0.5,
        fontSize: 15, color: "3f5054",
        bullet: { code: "2022" },
      });
    });
    if (slide.notes) frame.addNotes(slide.notes);
  }
  await pres.writeFile({ fileName: "presentation-outline.pptx" });
}
</script>

<template>
  <div v-if="artifact" class="slides">
    <header class="slides-title">
      <span>PRESENTATION / 汇报提纲</span>
      <div class="slides-head">
        <h2>{{ artifact.title }}</h2>
        <div class="slides-export">
          <button type="button" @click="exportMarkdown"><el-icon><Download /></el-icon>Markdown</button>
          <button type="button" @click="exportPptx"><el-icon><Download /></el-icon>PPTX</button>
        </div>
      </div>
      <p>共 {{ slides.length }} 页幻灯片，可导出 Markdown 或 PPTX。</p>
    </header>

    <div class="slide-grid">
      <article v-for="(slide, index) in slides" :key="index" class="slide-card">
        <div class="slide-index">{{ String(index + 1).padStart(2, "0") }}</div>
        <div class="slide-body">
          <h3>{{ slide.title }}</h3>
          <ul v-if="slide.bullets.length">
            <li v-for="(bullet, bulletIndex) in slide.bullets" :key="bulletIndex">
              <ScientificText :content="bullet" />
            </li>
          </ul>
          <p v-if="slide.notes" class="slide-notes">演讲备注：{{ slide.notes }}</p>
        </div>
      </article>
    </div>

    <PaperCitationList :citations="artifact.citations" @open="emit('openCitation', $event)" />
  </div>
  <el-empty v-else description="汇报提纲尚未生成" />
</template>

<style scoped>
.slides { max-width: 860px; margin: 0 auto; }
.slides-title { padding-bottom: 20px; border-bottom: 2px solid #183641; }
.slides-title > span { color: #b86243; font-size: 10px; letter-spacing: .2em; font-weight: 700; }
.slides-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 8px; }
.slides-head h2 { margin: 0; color: #183641; font-family: Georgia, "Noto Serif SC", serif; font-size: 21px; font-weight: 600; }
.slides-title p { margin: 8px 0 0; color: #7b8585; font-size: 12px; }
.slides-export { display: flex; gap: 8px; }
.slides-export button {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 5px 12px;
  border: 1px solid #d3cbbd;
  border-radius: 5px;
  background: #faf6ee;
  color: #4f6166;
  font-size: 12px;
  cursor: pointer;
}
.slides-export button:hover { border-color: #b75f40; color: #b75f40; }
.slide-grid { display: grid; gap: 16px; padding: 24px 0; }
.slide-card {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr);
  gap: 14px;
  padding: 18px;
  border: 1px solid #e0dbce;
  border-left: 3px solid #d9b48f;
  background: rgba(255,255,255,.6);
  border-radius: 6px;
}
.slide-index { color: #c16f50; font: 700 15px/1.4 Georgia, serif; }
.slide-body { min-width: 0; }
.slide-body h3 { margin: 0 0 10px; color: #1e3942; font-family: "Noto Serif SC", Georgia, serif; font-size: 17px; }
.slide-body ul { margin: 0; padding-left: 1.2em; }
.slide-body li { margin: 5px 0; color: #3f5054; line-height: 1.75; }
.slide-notes { margin: 10px 0 0; padding: 8px 10px; background: #f1ecdf; color: #6d7672; font-size: 12px; line-height: 1.6; border-radius: 4px; }
</style>