<script setup lang="ts">
/**
 * 聊天输入框
 *
 * Enter 发送 / Shift+Enter 换行 / 发送中禁用。
 */

import { ref } from "vue";

const props = defineProps<{
  loading: boolean;
}>();

const emit = defineEmits<{
  (e: "send", text: string): void;
}>();

const text = ref("");

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
}

function send() {
  const val = text.value.trim();
  if (!val || props.loading) return;
  emit("send", val);
  text.value = "";
}
</script>

<template>
  <div class="chat-input">
    <el-input
      v-model="text"
      type="textarea"
      :rows="3"
      placeholder="输入您的问题，Enter 发送，Shift+Enter 换行"
      :disabled="loading"
      resize="none"
      @keydown="onKeydown"
    />
    <el-button
      type="primary"
      :loading="loading"
      :disabled="!text.trim()"
      @click="send"
      class="send-btn"
    >
      发送
    </el-button>
  </div>
</template>

<style scoped>
.chat-input {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  padding: 16px;
  border-top: 1px solid var(--border-color);
  background: #fff;
}

.send-btn {
  flex-shrink: 0;
}
</style>
