<script setup lang="ts">
import { DocumentChecked, Warning } from "@element-plus/icons-vue";
import type { PaperCitation } from "@/types/paper";

defineProps<{ citations: PaperCitation[] }>();
const emit = defineEmits<{ open: [citation: PaperCitation] }>();
</script>

<template>
  <section v-if="citations.length" class="citation-section">
    <div class="citation-heading">证据索引 <span>{{ citations.length }}</span></div>
    <button
      v-for="(citation, index) in citations"
      :key="`${citation.chunk_id}-${index}`"
      type="button"
      class="citation-card"
      :class="{ muted: !citation.verified }"
      @click="citation.page !== null && emit('open', citation)"
    >
      <el-icon><DocumentChecked v-if="citation.verified" /><Warning v-else /></el-icon>
      <span class="citation-copy">
        <strong>{{ citation.section || "原文" }} · {{ citation.page ? `p.${citation.page}` : "页码未核实" }}</strong>
        <small>“{{ citation.quote }}”</small>
      </span>
      <span v-if="citation.page" class="jump">打开 →</span>
    </button>
  </section>
</template>

<style scoped>
.citation-section { margin-top: 28px; padding-top: 18px; border-top: 1px solid #ded9cb; }
.citation-heading { margin-bottom: 10px; color: #607078; font-size: 11px; letter-spacing: .12em; text-transform: uppercase; }
.citation-heading span { margin-left: 6px; padding: 1px 6px; border-radius: 10px; background: #e8e3d6; color: #223942; }
.citation-card { width: 100%; display: grid; grid-template-columns: 20px 1fr auto; gap: 10px; align-items: start; margin-top: 7px; padding: 11px 12px; border: 1px solid #dfd9ca; border-radius: 7px; background: rgba(255,255,255,.54); color: #263d46; text-align: left; cursor: pointer; transition: .18s ease; }
.citation-card:hover { transform: translateX(3px); border-color: #be7758; box-shadow: 0 5px 18px rgba(45,55,50,.07); }
.citation-card.muted { cursor: default; opacity: .68; }
.citation-copy { display: grid; gap: 4px; }
.citation-copy strong { font-size: 12px; }
.citation-copy small { color: #65747a; font-family: Georgia, serif; font-size: 11px; line-height: 1.5; }
.jump { color: #b45e3d; font-size: 10px; white-space: nowrap; }
</style>
