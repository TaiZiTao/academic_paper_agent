<script setup lang="ts">
/**
 * 消息列表 + 流式状态指示
 */

import { ref, watch, nextTick } from "vue";
import type { ChatMessage } from "@/types/chat";
import ChatMessageComponent from "./ChatMessage.vue";

const props = defineProps<{
  messages: ChatMessage[];
  loading: boolean;
  error: string;
  isStreaming?: boolean;
}>();

const listEl = ref<HTMLElement | null>(null);

function scrollToBottom() {
  nextTick(() => {
    if (listEl.value) {
      listEl.value.scrollTop = listEl.value.scrollHeight;
    }
  });
}

watch(() => props.messages.length, scrollToBottom);
// 流式内容更新时也滚底
watch(() => props.messages.map(m => m.content).join(), scrollToBottom);

defineExpose({ scrollToBottom });
</script>

<template>
  <div ref="listEl" class="msg-list">
    <div v-if="messages.length === 0 && !loading" class="msg-empty">
      <el-empty description="开始提问吧" />
    </div>

    <div v-for="msg in messages" :key="msg.id" class="msg-row" :class="msg.role">
      <ChatMessageComponent :message="msg" />
    </div>

    <!-- 流式加载指示 -->
    <div v-if="isStreaming" class="msg-row assistant">
      <div class="streaming-hint">● 正在生成...</div>
    </div>

    <div v-if="error" class="msg-error">
      <el-alert :title="error" type="error" :closable="false" />
    </div>
  </div>
</template>

<style scoped>
.msg-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.msg-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.msg-row { display: flex; }
.msg-row.user { justify-content: flex-end; }
.msg-row.assistant { justify-content: flex-start; }

.streaming-hint {
  padding: 8px 16px;
  color: var(--text-secondary);
  font-size: 13px;
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.msg-error { margin-top: 12px; }
</style>
