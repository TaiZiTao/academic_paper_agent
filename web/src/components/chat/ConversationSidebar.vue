<script setup lang="ts">
/**
 * 会话侧边栏
 *
 * Phase 13E：完整会话列表 + 新建/删除/切换。
 */

import { Delete } from "@element-plus/icons-vue";
import { ElMessageBox } from "element-plus";
import { useConversation } from "@/composables/useConversation";

const { items, currentId, isEmpty, create, remove, select } = useConversation();

const emit = defineEmits<{
  (e: "create"): void;
}>();

function onCreate() {
  const conv = create();
  emit("create");
  select(conv.id);
}

async function onDelete(id: string, title: string) {
  try {
    await ElMessageBox.confirm(`删除会话「${title}」？`, "确认", {
      type: "warning",
      confirmButtonText: "删除",
      cancelButtonText: "取消",
    });
    remove(id);
    // 同步删除后端数据
    fetch(`/api/v1/conversations/${id}`, { method: "DELETE" }).catch(() => {});
  } catch {
    // cancelled
  }
}

function formatTime(ts: string): string {
  const d = new Date(ts.includes("T") ? ts : ts.replace(" ", "T") + "Z");
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  if (diffMs < 60_000) return "刚刚";
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)}分钟前`;
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}
</script>

<template>
  <div class="conv-sidebar">
    <div class="conv-header">
      <el-button type="primary" size="small" style="width: 100%" @click="onCreate">
        新建会话
      </el-button>
    </div>

    <div class="conv-list">
      <!-- Empty -->
      <div v-if="isEmpty" class="conv-empty">
        <el-empty description="暂无会话" :image-size="50" />
      </div>

      <!-- List -->
      <div
        v-for="item in items"
        :key="item.id"
        class="conv-item"
        :class="{ active: item.id === currentId }"
        @click="select(item.id)"
      >
        <div class="conv-info">
          <div class="conv-title">{{ item.title }}</div>
          <div class="conv-time">{{ formatTime(item.updated_at) }}</div>
        </div>
        <el-button
          text
          size="small"
          class="conv-delete"
          @click.stop="onDelete(item.id, item.title)"
        >
          <el-icon><Delete /></el-icon>
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.conv-sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #fff;
  border-right: 1px solid var(--border-color);
}

.conv-header {
  padding: 12px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
}

.conv-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.conv-item {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  cursor: pointer;
  border-bottom: 1px solid #f5f5f5;
  transition: background 0.15s;
}
.conv-item:hover { background: #f5f7fa; }
.conv-item.active { background: #ecf5ff; }

.conv-info {
  flex: 1;
  min-width: 0;
}

.conv-title {
  font-size: 14px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-time {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}

.conv-delete {
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s;
}
.conv-item:hover .conv-delete { opacity: 1; }
</style>
