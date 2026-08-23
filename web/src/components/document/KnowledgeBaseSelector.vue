<script setup lang="ts">
/**
 * 知识库选择器
 *
 * 复用于文档上传、文档列表筛选等场景。
 * v-model 绑定选中的 KB ID。
 */

import { ref, onMounted } from "vue";
import { listKnowledgeBases } from "@/api/kb";
import type { KnowledgeBase } from "@/types/kb";

const selectedId = defineModel<number | null>({ default: null });

const kbList = ref<KnowledgeBase[]>([]);
const loading = ref(false);

onMounted(async () => {
  loading.value = true;
  try {
    const res = await listKnowledgeBases({ page_size: 100 });
    kbList.value = res.items;
  } catch {
    // 静默失败
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div class="kb-selector">
    <label class="selector-label">所属知识库</label>
    <el-select
      v-model="selectedId"
      placeholder="请选择知识库"
      :loading="loading"
      clearable
      style="width: 320px"
    >
      <el-option
        v-for="kb in kbList"
        :key="kb.id"
        :label="kb.name"
        :value="kb.id"
      />
    </el-select>
  </div>
</template>

<style scoped>
.kb-selector {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}

.selector-label {
  font-weight: 500;
  white-space: nowrap;
  color: var(--text-regular);
}
</style>
