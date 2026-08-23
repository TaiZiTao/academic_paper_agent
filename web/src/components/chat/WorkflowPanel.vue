<script setup lang="ts">
/**
 * 工作流面板 — 全库问答 LangGraph 节点状态
 * status: pending(待执行) → running(进行中) → completed(完成)
 */

export interface WorkflowNode {
  node: string;
  status: "pending" | "running" | "completed";
  extra?: Record<string, unknown>;
}

defineProps<{
  nodes: WorkflowNode[];
}>();

const NODE_LABELS: Record<string, string> = {
  intent_router: "意图路由",
  relevance_check: "相关性判定",
  general_chat_node: "自由对话",
  chat_node: "闲聊回复",
  catalog_node: "论文清单",
  direction_select: "方向选择",
  retrieve: "证据检索",
  relevance_evaluate: "相关性评估",
  rewrite_query: "查询改写",
  generate: "聚合生成",
  cite_verify: "引用校验",
};

function nodeLabel(name: string): string {
  return NODE_LABELS[name] || name;
}

/** 从节点 extra 里挑几个短字段做摘要(过滤条件/降级/意图), 大对象不进面板。 */
function summary(node: WorkflowNode): string {
  if (!node.extra) return "";
  const parts: string[] = [];
  const f = node.extra.filters;
  if (f && typeof f === "object") {
    const filters = f as Record<string, unknown>;
    const bits: string[] = [];
    if (filters.field) bits.push(`方向:${filters.field}`);
    if (filters.year_min) bits.push(`${filters.year_min}-${filters.year_max ?? "今"}`);
    if (bits.length) parts.push(bits.join(" "));
  }
  const degraded = node.extra.degraded;
  if (Array.isArray(degraded) && degraded.length > 0) {
    parts.push(`降级:${degraded.join(",")}`);
  }
  if (typeof node.extra.intent === "string" && node.extra.intent) {
    parts.push(`意图:${node.extra.intent}`);
  }
  return parts.join(" · ");
}
</script>

<template>
  <div class="info-panel">
    <h4>Workflow</h4>
    <el-divider style="margin: 8px 0" />

    <div v-if="nodes.length === 0" class="empty-hint">等待问答任务…</div>

    <div v-else class="wf-list">
      <div
        v-for="(n, i) in nodes"
        :key="i"
        class="wf-item"
        :class="'wf-' + n.status"
      >
        <span class="wf-dot">
          <template v-if="n.status === 'completed'">✓</template>
          <template v-else-if="n.status === 'running'">●</template>
          <template v-else>○</template>
        </span>
        <span class="wf-name">{{ nodeLabel(n.node) }}</span>
        <span v-if="summary(n)" class="wf-extra" :title="summary(n)">{{ summary(n) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.info-panel {
  padding: 16px;
}

.empty-hint {
  color: var(--text-secondary);
  font-size: 13px;
  padding: 8px 0;
}

.wf-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 4px;
}

.wf-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  min-width: 0;
}

.wf-dot {
  width: 16px;
  flex-shrink: 0;
  text-align: center;
  color: var(--text-secondary);
  font-size: 12px;
}

.wf-running .wf-dot {
  color: var(--el-color-primary);
  animation: wf-pulse 1.2s ease-in-out infinite;
}

.wf-completed .wf-dot {
  color: var(--el-color-success);
}

.wf-name {
  flex-shrink: 0;
}

.wf-extra {
  color: var(--text-secondary);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@keyframes wf-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
</style>
