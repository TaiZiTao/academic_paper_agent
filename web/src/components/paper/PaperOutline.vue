<script setup lang="ts">
import { computed } from "vue";
import type { PaperSection, PaperSummary } from "@/types/paper";

const props = defineProps<{
  paper: PaperSummary;
  sections: PaperSection[];
  activePage: number;
}>();

const emit = defineEmits<{ openPage: [page: number] }>();

// 按 level 构建父子层级: level 2+ 的章节(如实验下的 Discussion)挂在最近的上层章节下
const tree = computed(() => {
  const roots: (PaperSection & { children: PaperSection[] })[] = [];
  const stack: (PaperSection & { children: PaperSection[] })[] = [];
  for (const section of props.sections) {
    const node = { ...section, children: [] as PaperSection[] };
    while (stack.length && stack[stack.length - 1].level >= node.level) stack.pop();
    if (stack.length) stack[stack.length - 1].children.push(node);
    else roots.push(node);
    stack.push(node);
  }
  return roots;
});
</script>

<template>
  <aside class="outline-panel">
    <div class="paper-kicker">PAPER DOSSIER</div>
    <h1>{{ paper.title || paper.original_filename }}</h1>
    <p class="authors">{{ paper.authors.join(" · ") || "作者信息未识别" }}</p>
    <div class="paper-facts">
      <span>{{ paper.language === "zh" ? "中文" : paper.language === "en" ? "English" : "中英混合" }}</span>
      <span>{{ paper.page_count }} 页</span>
    </div>

    <div class="divider"><span>SECTION MAP</span></div>
    <nav class="section-list" aria-label="论文章节">
      <template v-for="(section, topIndex) in tree" :key="section.id">
        <button
          type="button"
          class="section-item"
          :class="{ active: activePage >= section.page_start && activePage <= section.page_end }"
          @click="emit('openPage', section.page_start)"
        >
          <span class="ordinal">{{ String(topIndex + 1).padStart(2, "0") }}</span>
          <span class="section-copy">
            <strong>{{ section.title }}</strong>
            <small>p.{{ section.page_start }}–{{ section.page_end }}</small>
          </span>
        </button>
        <button
          v-for="child in section.children"
          :key="child.id"
          type="button"
          class="section-item section-item--child"
          :class="{ active: activePage >= child.page_start && activePage <= child.page_end }"
          @click="emit('openPage', child.page_start)"
        >
          <span class="ordinal child-mark">↳</span>
          <span class="section-copy">
            <strong>{{ child.title }}</strong>
            <small>p.{{ child.page_start }}–{{ child.page_end }}</small>
          </span>
        </button>
      </template>
    </nav>
  </aside>
</template>

<style scoped>
.outline-panel { height: 100%; padding: 28px 22px; background: #122631; color: #f2efe6; overflow-y: auto; }
.paper-kicker { color: #d98d68; font-size: 10px; letter-spacing: .24em; font-weight: 700; }
h1 { margin: 12px 0 10px; font-family: "Noto Serif SC", "Source Han Serif SC", Georgia, serif; font-size: 21px; line-height: 1.42; font-weight: 600; }
.authors { color: #aebcc0; font-size: 12px; line-height: 1.6; }
.paper-facts { display: flex; gap: 8px; margin-top: 16px; }
.paper-facts span { padding: 5px 9px; border: 1px solid rgba(255,255,255,.14); border-radius: 999px; color: #cfdbdd; font-size: 11px; }
.divider { display: flex; align-items: center; gap: 10px; margin: 30px 0 12px; color: #71868c; font-size: 9px; letter-spacing: .18em; }
.divider::after { content: ""; flex: 1; height: 1px; background: rgba(255,255,255,.12); }
.section-list { display: grid; gap: 4px; }
.section-list .section-item { width: 100%; display: flex; gap: 12px; align-items: flex-start; padding: 10px 9px; border: 0; border-left: 2px solid transparent; background: transparent; color: inherit; text-align: left; cursor: pointer; transition: .2s ease; }
.section-list .section-item:hover, .section-list .section-item.active { border-left-color: #df8058; background: rgba(255,255,255,.06); }
.section-list .section-item--child { padding-left: 26px; }
.section-list .section-item--child .section-copy strong { font-weight: 400; color: #cfd8da; }
.child-mark { font-size: 12px; line-height: 1.5; }
.ordinal { color: #71868c; font: 11px/1.5 ui-monospace, monospace; }
.section-copy { display: grid; gap: 3px; min-width: 0; }
.section-copy strong { overflow: hidden; text-overflow: ellipsis; font-size: 12px; line-height: 1.5; font-weight: 500; }
.section-copy small { color: #819399; font-size: 10px; }
</style>
