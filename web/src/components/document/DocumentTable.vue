<script setup lang="ts">
/**
 * 文档列表表格
 */

import type { DocumentInfo } from "@/types/document";

defineProps<{
  items: DocumentInfo[];
  loading: boolean;
  error: string;
}>();

const emit = defineEmits<{
  (e: "delete", item: DocumentInfo): void;
}>();

/** 状态标签类型映射 */
function statusType(status: string): "success" | "warning" | "danger" | "info" {
  switch (status) {
    case "completed": return "success";
    case "parsing": case "embedding": return "warning";
    case "failed": return "danger";
    default: return "info";
  }
}
</script>

<template>
  <el-table v-loading="loading" :data="items" stripe style="width: 100%">
    <el-table-column type="index" label="#" width="50" align="center" />
    <el-table-column prop="original_filename" label="文件名" min-width="200" show-overflow-tooltip />
    <el-table-column prop="kb_name" label="所属知识库" min-width="140" show-overflow-tooltip />
    <el-table-column prop="extension" label="类型" width="80" align="center" />
    <el-table-column label="大小" width="100" align="center">
      <template #default="{ row }">
        {{ row.size ? (row.size / 1024).toFixed(1) + " KB" : "—" }}
      </template>
    </el-table-column>
    <el-table-column label="状态" width="110" align="center">
      <template #default="{ row }">
        <el-tag :type="statusType(row.status)" size="small">
          {{ row.status === "completed" ? "已完成" : row.status }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column prop="chunk_count" label="片段数" width="80" align="center" />
    <el-table-column prop="created_at" label="上传时间" width="170" align="center" />
    <el-table-column label="操作" width="100" align="center" fixed="right">
      <template #default="{ row }">
        <el-button text type="danger" size="small" @click="emit('delete', row)">
          删除
        </el-button>
      </template>
    </el-table-column>

    <template #empty>
      <el-empty :description="error || '暂无文档，请先上传'" />
    </template>
  </el-table>
</template>
