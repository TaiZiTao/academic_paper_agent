<script setup lang="ts">
/**
 * 智能问答页面 — Phase 14 流式 + Phase 15 UI 优化
 */

import { onMounted, onUnmounted, watch } from "vue";
import { useChat } from "@/composables/useChat";
import { useConversation } from "@/composables/useConversation";
import ConversationSidebar from "@/components/chat/ConversationSidebar.vue";
import ChatMessageList from "@/components/chat/ChatMessageList.vue";
import ChatInput from "@/components/chat/ChatInput.vue";
import CitationPanel from "@/components/chat/CitationPanel.vue";
import WorkflowPanel from "@/components/chat/WorkflowPanel.vue";

// --- 会话 ---
const conv = useConversation();

// --- 聊天（流式） ---
const {
  messages, loading, error,
  listRef, sendMessage, loadHistory,
  citations, isStreaming, abort, workflowNodes,
} = useChat(() => conv.currentId.value);

let msgCount = 0;
async function onSend(query: string) {
  const sid = conv.currentId.value;
  await sendMessage(query);
  if (sid) {
    if (msgCount === 0) conv.updateTitle(sid, query);
    conv.touch(sid);
    msgCount++;
  }
}

// 首次加载：从后端恢复会话列表（如果 localStorage 丢失），然后加载历史或创建新会话
onMounted(async () => {
  await conv.init();
  if (conv.isEmpty.value) {
    conv.create();
  } else if (conv.currentId.value) {
    await loadHistory(conv.currentId.value);
  }
});

// 切换会话时恢复历史
watch(() => conv.currentId.value, (newId) => {
  msgCount = 0;
  abort();
  messages.value = [];
  citations.value = [];
  if (newId) loadHistory(newId);
});

onUnmounted(() => abort());
</script>

<template>
  <div class="chat-page">
    <div class="chat-left">
      <ConversationSidebar />
    </div>

    <div class="chat-center">
      <ChatMessageList
        ref="listRef"
        :messages="messages"
        :loading="loading"
        :error="error"
        :is-streaming="isStreaming"
      />
      <ChatInput :loading="loading || isStreaming" @send="onSend" />
    </div>

    <div class="chat-right">
      <CitationPanel :citations="citations" />
      <WorkflowPanel :nodes="workflowNodes" />
    </div>
  </div>
</template>

<style scoped>
/* flex: 1 填满 el-main，避免 router-view 阻断 height: 100% 链 */
.chat-page {
  display: flex;
  flex: 1;
  overflow: hidden;
  padding: 0 20px;
}

.chat-left {
  width: 260px;
  flex-shrink: 0;
}

.chat-center {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--chat-bg, #fafafa);
}

.chat-right {
  width: 280px;
  flex-shrink: 0;
  overflow-y: auto;
  background: #fff;
  border-left: 1px solid var(--border-color);
  padding: 0 20px;
}
</style>
