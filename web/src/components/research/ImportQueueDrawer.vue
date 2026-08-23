<script setup lang="ts">
import { computed } from "vue";
import { RefreshRight } from "@element-plus/icons-vue";
import type { ImportTask } from "@/types/research";

const props = defineProps<{
  modelValue: boolean;
  imports: ImportTask[];
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "retry", id: number): void;
}>();

// v-model 桥接: el-drawer 直接写 v-model="visible"
const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit("update:modelValue", v),
});

const statusMeta: Record<ImportTask["status"], { label: string; tone: string }> = {
  pending: { label: "排队中", tone: "neutral" },
  downloading: { label: "下载中", tone: "working" },
  parsing: { label: "解析中", tone: "working" },
  done: { label: "已入库", tone: "ready" },
  failed: { label: "失败", tone: "failed" },
};
</script>

<template>
  <el-drawer v-model="visible" title="导入队列" size="420px">
    <div class="import-list">
      <div v-for="task in imports" :key="task.id" class="import-item">
        <div class="import-top">
          <span class="import-title">{{ task.title }}</span>
          <span :class="['import-pill', statusMeta[task.status].tone]">{{ statusMeta[task.status].label }}</span>
        </div>
        <el-progress :percentage="task.progress" :stroke-width="6" :show-text="false" />
        <div class="import-foot">
          <span v-if="task.error_message" class="import-error">{{ task.error_message }}</span>
          <span v-else-if="task.paper_id">paper #{{ task.paper_id }}</span>
          <el-button v-if="task.status === 'failed'" text circle size="small" @click="emit('retry', task.id)">
            <el-icon><RefreshRight /></el-icon>
          </el-button>
        </div>
      </div>
      <el-empty v-if="imports.length === 0" description="暂无导入任务" />
    </div>
  </el-drawer>
</template>

<style scoped>
.import-list { display: grid; gap: 14px; }
.import-item { padding: 12px 14px; border: 1px solid #e1ddd2; border-radius: 8px; }
.import-top { display: flex; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
.import-title { font-size: 13px; line-height: 1.5; color: #1d333c; }
.import-pill { font-size: 10px; white-space: nowrap; }
.import-pill.working { color: #d79245; }
.import-pill.ready { color: #3d8e75; }
.import-pill.failed { color: #bc513f; }
.import-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 6px; font-size: 11px; color: #929a98; }
.import-error { color: #ad4f3c; font-size: 11px; }
</style>
