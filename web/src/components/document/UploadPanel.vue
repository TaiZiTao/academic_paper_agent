<script setup lang="ts">
/**
 * 文件上传面板
 *
 * 支持拖拽和点击上传，带文件类型/大小校验。
 */

import { ref } from "vue";
import { UploadFilled } from "@element-plus/icons-vue";
import { ALLOWED_EXTENSIONS, MAX_FILE_SIZE } from "@/types/document";

const emit = defineEmits<{
  (e: "file-selected", file: File | null): void;
}>();

const dragOver = ref(false);
const inputRef = ref<HTMLInputElement | null>(null);

const acceptStr = ALLOWED_EXTENSIONS.join(",");
const maxSizeMB = (MAX_FILE_SIZE / 1024 / 1024).toFixed(0);

function onDragOver(e: DragEvent) {
  e.preventDefault();
  dragOver.value = true;
}
function onDragLeave() {
  dragOver.value = false;
}
function onDrop(e: DragEvent) {
  e.preventDefault();
  dragOver.value = false;
  const f = e.dataTransfer?.files?.[0];
  if (f) emit("file-selected", f);
}
function onClick() {
  inputRef.value?.click();
}
function onInputChange(e: Event) {
  const target = e.target as HTMLInputElement;
  const f = target.files?.[0] ?? null;
  emit("file-selected", f);
}
</script>

<template>
  <div
    class="upload-panel"
    :class="{ 'drag-over': dragOver }"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop"
    @click="onClick"
  >
    <input
      ref="inputRef"
      type="file"
      :accept="acceptStr"
      class="upload-input"
      @change="onInputChange"
    />

    <el-icon :size="48" color="#c0c4cc">
      <UploadFilled />
    </el-icon>
    <p class="upload-text">拖拽文件到这里，或点击上传</p>
    <p class="upload-hint">
      支持 {{ ALLOWED_EXTENSIONS.join("、") }} 格式，最大 {{ maxSizeMB }}MB
    </p>
  </div>
</template>

<style scoped>
.upload-panel {
  border: 2px dashed var(--border-color);
  border-radius: 8px;
  padding: 40px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.3s, background 0.3s;
}
.upload-panel:hover,
.upload-panel.drag-over {
  border-color: var(--color-primary);
  background: rgba(64, 158, 255, 0.04);
}

.upload-input {
  display: none;
}

.upload-text {
  margin-top: 12px;
  font-size: 15px;
  color: var(--text-regular);
}

.upload-hint {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
