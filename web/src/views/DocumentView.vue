<script setup lang="ts">
/**
 * 文档管理 — 全量文件管理，不分库，只搜索+删除
 */

import { onMounted } from "vue";
import { useDocumentList } from "@/composables/useDocumentList";
import DocumentTable from "@/components/document/DocumentTable.vue";

const {
  items, total, loading, error,
  search, page, pageSize,
  fetchList, onSearch, onPageChange, onPageSizeChange, handleDelete,
} = useDocumentList();

onMounted(() => fetchList());
</script>

<template>
  <div class="doc-page">
    <div class="doc-header">
      <h2>文档管理</h2>
    </div>

    <div class="doc-toolbar">
      <el-input
        v-model="search"
        placeholder="搜索文件名"
        clearable
        style="width: 280px"
        @keyup.enter="onSearch"
      />
      <el-button type="primary" @click="onSearch">搜索</el-button>
    </div>

    <el-card shadow="never">
      <DocumentTable :items="items" :loading="loading" :error="error" @delete="handleDelete" />

      <div v-if="total > 0" class="doc-pagination">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[5, 10, 20]"
          layout="total, sizes, prev, pager, next"
          small
          @current-change="onPageChange"
          @size-change="onPageSizeChange"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.doc-page { max-width: 1200px; padding: 20px; }
.doc-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.doc-toolbar { display: flex; gap: 8px; margin-bottom: 16px; }
.doc-pagination { display: flex; justify-content: flex-end; margin-top: 16px; }
</style>
