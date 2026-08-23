<script setup lang="ts">
import { computed } from "vue";
import { paperPdfUrl } from "@/api/paper";

const props = defineProps<{ paperId: number; page: number; title: string }>();
const src = computed(() => `${paperPdfUrl(props.paperId)}#page=${props.page}&view=FitH`);
</script>

<template>
  <section class="pdf-shell">
    <header>
      <div><span>ORIGINAL PDF</span><strong>原文 · p.{{ page }}</strong></div>
      <a :href="src" target="_blank" rel="noopener">新窗口打开 ↗</a>
    </header>
    <iframe :key="`${paperId}-${page}`" :src="src" :title="`${title} 第 ${page} 页`" />
  </section>
</template>

<style scoped>
.pdf-shell { height: 100%; display: flex; flex-direction: column; background: #273238; }
header { min-height: 58px; display: flex; justify-content: space-between; align-items: center; padding: 10px 15px; border-bottom: 1px solid rgba(255,255,255,.1); color: white; }
header div { display: grid; gap: 2px; }
header span { color: #98a8ad; font-size: 9px; letter-spacing: .18em; }
header strong { font-size: 12px; font-weight: 500; }
header a { color: #e4a37e; font-size: 11px; }
iframe { flex: 1; width: 100%; border: 0; background: #4b5559; }
</style>
