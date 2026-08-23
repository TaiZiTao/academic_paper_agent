<script setup lang="ts">
import { ref, onMounted } from "vue";
import { listPapers } from "@/api/paper";

interface PaperStats { total: number; fields: number; ready: number; processing: number }

const stats = ref<PaperStats>({ total: 0, fields: 0, ready: 0, processing: 0 });
const loading = ref(false);

onMounted(async () => {
  loading.value = true;
  try {
    const data = await listPapers({ page_size: 100 });
    const items = data.items || [];
    stats.value = {
      total: Number(data.total) || 0,
      fields: (data.fields || []).filter((f) => f !== "未分类").length,
      ready: items.filter((p) => p.status === "ready").length,
      processing: items.filter((p) => p.status !== "ready" && p.status !== "failed").length,
    };
  } catch { /* keep defaults */ }
  loading.value = false;
});
</script>

<template>
  <div class="home">
    <h2 style="margin-bottom: 16px">欢迎使用论文智答</h2>
    <el-row :gutter="16" v-loading="loading">
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="论文总数" :value="stats.total" suffix="篇" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="研究方向" :value="stats.fields" suffix="个" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="可精读" :value="stats.ready" suffix="篇" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="处理中" :value="stats.processing" suffix="篇" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.home { max-width: 1200px; padding: 20px; }
</style>
