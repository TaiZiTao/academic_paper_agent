<script setup lang="ts">
/**
 * 引用面板 — 全库问答引用数据(PaperCitation[])
 */

import type { PaperCitation } from "@/types/paper";

defineProps<{
  citations: PaperCitation[];
}>();
</script>

<template>
  <div class="info-panel">
    <h4>引用来源</h4>
    <el-divider style="margin: 8px 0" />

    <div v-if="citations.length === 0" class="empty-hint">暂无引用</div>

    <div v-for="(c, i) in citations" :key="i" class="citation-item">
      <el-tag size="small" type="info" class="citation-tag">
        {{ c.paper_title }}{{ c.page ? ` p.${c.page}` : "" }}
      </el-tag>
    </div>
  </div>
</template>

<style scoped>
.info-panel { padding: 16px; }

.empty-hint {
  color: var(--text-secondary);
  font-size: 13px;
  padding: 8px 0;
}

.citation-item {
  margin: 4px 0;
}

.citation-tag {
  font-family: monospace;
  font-size: 11px;
}
</style>
