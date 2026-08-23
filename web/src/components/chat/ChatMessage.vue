<script setup lang="ts">
/**
 * 单条聊天消息
 *
 * 用户消息：右侧蓝色气泡
 * 助手消息：左侧灰色气泡 + Markdown 渲染
 */

import type { ChatMessage } from "@/types/chat";

import { computed } from "vue";
import { marked } from "marked";

const props = defineProps<{
  message: ChatMessage;
}>();

function mdToHtml(text: string): string {
  if (!text) return "";
  // 用户消息不渲染 MD
  if (props.message.role === "user") return text;
  return marked(text) as string;
}

const uniqueCitations = computed(() => {
  const seen = new Set<string>();
  return (props.message.citations || []).filter((c) => {
    const key = `${c.paper_id}:${c.chunk_id || c.section || ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
});

/** 格式化时间（UTC → 本地） */
function formatTime(ts: string): string {
  const d = new Date(ts.includes("T") ? ts : ts.replace(" ", "T") + "Z");
  return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}
</script>

<template>
  <div class="chat-msg" :class="message.role">
    <div class="msg-body">
      <div class="msg-bubble" :class="message.role">
        <div class="msg-text" v-html="mdToHtml(message.content)" />

        <div v-if="uniqueCitations.length" class="msg-citations">
          <el-divider style="margin: 8px 0" />
          <span class="citations-label">引用来源：</span>
          <el-tag
            v-for="(c, i) in uniqueCitations"
            :key="i"
            size="small"
            type="info"
            style="margin: 2px"
          >
            {{ c.paper_title }}{{ c.page ? `（p.${c.page}）` : "" }}
          </el-tag>
        </div>
      </div>
      <div class="msg-time">{{ formatTime(message.timestamp) }}</div>
    </div>
  </div>
</template>

<style scoped>
.chat-msg {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
  max-width: 80%;
}
.chat-msg.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}
.chat-msg.assistant {
  align-self: flex-start;
}

.msg-body {
  display: flex;
  flex-direction: column;
}

.msg-bubble {
  padding: 12px 16px;
  border-radius: 8px;
  line-height: 1.6;
  word-break: break-word;
}
.msg-bubble.user {
  background: var(--color-primary, #409eff);
  color: #fff;
}
.msg-bubble.assistant {
  background: var(--msg-asst-bg, #f0f2f5);
  color: var(--text-primary);
}

.msg-time {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 4px;
}

.user .msg-time { text-align: right; }

.citations-label {
  font-size: 12px;
  color: var(--text-secondary);
}

/* Markdown 渲染样式 */
.msg-bubble.assistant :deep(h1) { font-size: 1.4em; margin: 0.6em 0 0.3em; }
.msg-bubble.assistant :deep(h2) { font-size: 1.2em; margin: 0.5em 0 0.2em; }
.msg-bubble.assistant :deep(h3) { font-size: 1.1em; margin: 0.4em 0 0.2em; }
.msg-bubble.assistant :deep(p) { margin: 0.4em 0; }
.msg-bubble.assistant :deep(ul), .msg-bubble.assistant :deep(ol) { padding-left: 1.5em; margin: 0.3em 0; }
.msg-bubble.assistant :deep(li) { margin: 0.2em 0; }
.msg-bubble.assistant :deep(code) { background: rgba(0,0,0,0.06); padding: 2px 5px; border-radius: 3px; font-size: 0.9em; }
.msg-bubble.assistant :deep(pre) { background: rgba(0,0,0,0.06); padding: 10px; border-radius: 6px; overflow-x: auto; }
.msg-bubble.assistant :deep(pre code) { background: none; padding: 0; }
.msg-bubble.assistant :deep(table) { border-collapse: collapse; width: 100%; margin: 0.5em 0; }
.msg-bubble.assistant :deep(th), .msg-bubble.assistant :deep(td) { border: 1px solid #ddd; padding: 6px 10px; text-align: left; }
.msg-bubble.assistant :deep(th) { background: rgba(0,0,0,0.04); font-weight: 600; }
.msg-bubble.assistant :deep(strong) { font-weight: 700; }
</style>
