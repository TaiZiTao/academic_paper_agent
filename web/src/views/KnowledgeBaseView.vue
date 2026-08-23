<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useKnowledgeBase } from "@/composables/useKnowledgeBase";
import { useDocumentList } from "@/composables/useDocumentList";
import { useDocumentUpload } from "@/composables/useDocumentUpload";
import KnowledgeBaseDialog from "@/components/kb/KnowledgeBaseDialog.vue";
import UploadPanel from "@/components/document/UploadPanel.vue";
import DocumentTable from "@/components/document/DocumentTable.vue";

// --- 知识库 ---
const {
  items, loading: kbLoading, error: kbError,
  dialogVisible, dialogTitle, form,
  fetchList: fetchKB,
  openCreateDialog, openEditDialog, submitForm, handleDelete,
} = useKnowledgeBase();

const activeIndex = ref(0);
const selectedId = computed(() => items.value[activeIndex.value]?.id ?? null);

const loadingKB = ref(false);
function onDocSearch() { fetchDocs(); }

async function loadKB() {
  loadingKB.value = true;
  await fetchKB();
  loadingKB.value = false;
  // KB 加载完后拉对应文档
  if (selectedId.value) {
    filterKBId.value = selectedId.value;
    await fetchDocs();
  }
}

onMounted(() => loadKB());

// --- 文档 ---
const {
  items: docItems, total: docTotal, loading: docLoading, error: docError,
  filterKBId, page: docPage, pageSize: docPageSize,
  search: docSearch, fetchList: fetchDocs, handleDelete: handleDocDelete, onUploadSuccess,
} = useDocumentList();

// 用户切 KB → 刷新文档
function onCarouselChange(i: number) {
  activeIndex.value = i;
  const id = items.value[i]?.id;
  if (id) {
    filterKBId.value = id;
    fetchDocs();
  }
}

// --- 上传 ---
const uploadDialogVisible = ref(false);
const {
  file: upFile, uploading: upUploading, progress: upProgress, uploaded: upUploaded,
  onFileSelected: upOnFile, startUpload: upStart, reset: upReset,
} = useDocumentUpload();

function openUpload() {
  upReset();
  uploadDialogVisible.value = true;
}
async function onUpload() {
  const ok = await upStart(selectedId.value);
  if (ok) onUploadSuccess();
}

// --- 操作 ---
function onEditKB() {
  const item = items.value.find(i => i.id === selectedId.value);
  if (item) openEditDialog(item);
}
function onDeleteKB() {
  const item = items.value.find(i => i.id === selectedId.value);
  if (item) handleDelete(item);
}

</script>

<template>
  <div class="kb-page">
    <h2>知识库管理</h2>

    <!-- KB 操作栏 -->
    <div class="kb-header">
      <div class="kb-actions">
        <el-button type="primary" @click="openCreateDialog">新建知识库</el-button>
        <el-button :disabled="!selectedId" @click="onEditKB">编辑</el-button>
        <el-button :disabled="!selectedId" type="danger" plain @click="onDeleteKB">删除</el-button>
      </div>
    </div>

    <!-- KB 走马灯 -->
    <div v-loading="kbLoading" class="kb-carousel-wrap">
      <el-empty v-if="items.length === 0 && !kbLoading" :description="kbError || '暂无知识库，点击新建开始'" />
      <el-carousel
        v-else
        :interval="0"
        type="card"
        height="240px"
        :loop="items.length >= 5"
        indicator-position="none"
        @change="onCarouselChange"
      >
        <el-carousel-item v-for="(item, i) in items" :key="item.id">
          <div class="kb-card" :style="{ background: ['#2c3e50','#1abc9c','#2980b9','#8e44ad','#e74c3c','#d35400','#27ae60','#2ecc71'][i % 8] }">
            <h3>{{ item.name }}</h3>
            <p>{{ item.description || '暂无描述' }}</p>
          </div>
        </el-carousel-item>
      </el-carousel>
    </div>

    <el-divider />

    <!-- 文档区 -->
    <div class="doc-section">
      <div class="doc-header">
        <h3>文档管理</h3>
        <div class="doc-header-right">
          <el-input
            v-model="docSearch"
            placeholder="搜索文档"
            clearable
            size="small"
            style="width: 220px"
            @keyup.enter="onDocSearch"
          />
          <el-button size="small" @click="onDocSearch">搜索</el-button>
          <el-button type="primary" size="small" :disabled="!selectedId" @click="openUpload">上传文档</el-button>
        </div>
      </div>

      <el-card v-if="selectedId" shadow="never">
        <DocumentTable
          :items="docItems"
          :loading="docLoading"
          :error="docError"
          @delete="handleDocDelete"
        />
        <div v-if="docTotal > 0" style="display:flex;justify-content:flex-end;margin-top:16px">
          <el-pagination
            v-model:current-page="docPage"
            v-model:page-size="docPageSize"
            :total="docTotal"
            :page-sizes="[5,10,20]"
            layout="total, sizes, prev, pager, next"
            small
            @current-change="fetchDocs"
            @size-change="fetchDocs"
          />
        </div>
      </el-card>
      <el-empty v-else description="请先选择一个知识库" :image-size="60" />
    </div>

    <!-- KB Dialog -->
    <KnowledgeBaseDialog
      v-model:visible="dialogVisible"
      v-model:form-name="form.name"
      v-model:form-desc="form.description"
      :title="dialogTitle"
      :loading="kbLoading"
      @submit="submitForm"
    />

    <!-- 上传 Dialog -->
    <el-dialog v-model="uploadDialogVisible" title="上传文档" width="520px" @closed="upReset()">
      <UploadPanel @file-selected="upOnFile" />
      <div v-if="upFile" style="margin-top:12px">
        <el-tag type="info">{{ upFile.name }}（{{ (upFile.size / 1024).toFixed(1) }} KB）</el-tag>
      </div>
      <div v-if="upUploading" style="margin-top:12px">
        <el-progress :percentage="upProgress" :stroke-width="6" />
        <p style="font-size:12px;color:#999;margin-top:4px">正在解析文件...</p>
      </div>
      <div v-if="upUploaded" style="margin-top:12px">
        <el-alert type="success" :closable="false" show-icon :title="`${upUploaded.filename} — ${upUploaded.chunks} 个片段已索引`" />
      </div>
      <template #footer>
        <el-button @click="uploadDialogVisible = false">关闭</el-button>
        <el-button v-if="upFile && !upUploaded" type="primary" :loading="upUploading" @click="onUpload">开始上传</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.kb-page { padding: 20px; max-height: calc(100vh - var(--header-height)); overflow-y: auto; }
.kb-header { display: flex; justify-content: flex-start; align-items: center; margin: 16px 0; }
.kb-actions { display: flex; gap: 8px; }
.kb-carousel-wrap { min-height: 200px; perspective: 1200px; }

.kb-card {
  width: 100%; height: 100%; border-radius: 12px;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 30px; color: #fff; text-align: center;
}
.kb-card h3 { font-size: 22px; font-weight: 700; margin-bottom: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; width: 100%; text-shadow: 0 1px 3px rgba(0,0,0,0.3); }
.kb-card p { font-size: 13px; opacity: 0.85; line-height: 1.6; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; width: 100%; }

:deep(.el-carousel__item--card) { transition: all 0.5s cubic-bezier(0.25, 0.46, 0.45, 0.94); }
:deep(.el-carousel__item--card:not(.is-active):hover) { transform: translateY(-6px) scale(1.02); }

.doc-section { margin-top: 24px; }
.doc-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.doc-header-right { display: flex; gap: 8px; align-items: center; }
</style>
