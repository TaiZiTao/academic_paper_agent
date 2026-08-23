<script setup lang="ts">
import { ref, onMounted } from "vue";

interface Settings {
  app_name: string; app_version: string;
  llm_model: string; llm_provider: string;
  embedding_model: string;
  chunk_size: number; chunk_overlap: number;
  top_k: number; vector_weight: number; keyword_weight: number;
}

const activeTab = ref("system");
const settings = ref<Settings | null>(null);
const loading = ref(false);
const health = ref<{ status: string; app_name: string; version: string } | null>(null);

onMounted(async () => {
  loading.value = true;
  try {
    const [s, h] = await Promise.all([
      (await import("@/api/index")).default.get<Settings>("/settings"),
      fetch("/health").then(r => r.json()),
    ]);
    settings.value = s.data;
    health.value = h;
  } catch { /* keep defaults */ }
  loading.value = false;
});
</script>

<template>
  <div class="settings-page">
    <h2 style="margin-bottom: 16px">系统设置</h2>
    <el-tabs v-model="activeTab" v-loading="loading">
      <el-tab-pane label="系统信息" name="system">
        <el-card shadow="never">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="应用名称">{{ settings?.app_name || '论文智答' }}</el-descriptions-item>
            <el-descriptions-item label="版本号">{{ settings?.app_version || '0.1.0' }}</el-descriptions-item>
            <el-descriptions-item label="技术栈">FastAPI + LangGraph + RAG + SQLite</el-descriptions-item>
            <el-descriptions-item label="前端框架">Vue 3 + Vite + Element Plus</el-descriptions-item>
            <el-descriptions-item label="检索引擎">
              <el-tag size="small" type="success">FAISS</el-tag>
              <el-tag size="small" type="success" style="margin-left:4px">BM25</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="数据库">SQLite (aiosqlite)</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="模型配置" name="model">
        <el-card shadow="never">
          <el-descriptions :column="1" border>
            <el-descriptions-item label="LLM Provider">{{ settings?.llm_provider || '—' }}</el-descriptions-item>
            <el-descriptions-item label="LLM Model">{{ settings?.llm_model || '—' }}</el-descriptions-item>
            <el-descriptions-item label="Embedding Model">{{ settings?.embedding_model || '—' }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="RAG 配置" name="rag">
        <el-card shadow="never">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="Chunk Size">{{ settings?.chunk_size || '—' }}</el-descriptions-item>
            <el-descriptions-item label="Chunk Overlap">{{ settings?.chunk_overlap || '—' }}</el-descriptions-item>
            <el-descriptions-item label="Top K">{{ settings?.top_k || '—' }}</el-descriptions-item>
            <el-descriptions-item label="检索策略">Hybrid (FAISS + BM25)</el-descriptions-item>
            <el-descriptions-item label="Vector Weight">{{ settings?.vector_weight ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="Keyword Weight">{{ settings?.keyword_weight ?? '—' }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="服务状态" name="service">
        <el-card shadow="never">
          <el-descriptions v-if="health" :column="1" border>
            <el-descriptions-item label="API 服务">
              <el-tag :type="health.status === 'ok' ? 'success' : 'danger'" size="small">{{ health.status === 'ok' ? '运行中' : health.status }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="应用名称">{{ health.app_name }}</el-descriptions-item>
            <el-descriptions-item label="版本">{{ health.version }}</el-descriptions-item>
          </el-descriptions>
          <el-empty v-else description="无法获取服务状态" :image-size="60" />
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.settings-page { max-width: 900px; padding: 20px; }
</style>
