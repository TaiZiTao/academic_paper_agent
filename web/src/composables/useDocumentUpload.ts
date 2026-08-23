/**
 * 文档上传 Composable
 *
 * 负责：文件校验、上传、进度跟踪、状态管理。
 * View 只负责展示 UI。
 */

import { ref } from "vue";
import { ElMessage } from "element-plus";
import { uploadDocument } from "@/api/document";
import { ALLOWED_EXTENSIONS, MAX_FILE_SIZE } from "@/types/document";

export function useDocumentUpload() {
  // --- 状态 ---
  const selectedKBId = ref<number | null>(null);
  const file = ref<File | null>(null);
  const uploading = ref(false);
  const progress = ref(0);
  const uploaded = ref<{ filename: string; chunks: number } | null>(null);

  /** 文件校验 */
  function validateFile(f: File): string | null {
    const ext = "." + f.name.split(".").pop()?.toLowerCase();
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      return `不支持的文件类型：${ext}，当前仅支持 ${ALLOWED_EXTENSIONS.join("、")}`;
    }
    if (f.size > MAX_FILE_SIZE) {
      const sizeMB = (f.size / 1024 / 1024).toFixed(1);
      return `文件过大：${sizeMB}MB，最大允许 10MB`;
    }
    return null;
  }

  /** 选择文件 */
  function onFileSelected(f: File | null) {
    uploaded.value = null;
    if (!f) {
      file.value = null;
      return;
    }
    const err = validateFile(f);
    if (err) {
      ElMessage.error(err);
      file.value = null;
      return;
    }
    file.value = f;
  }

  /** 执行上传，kbId 参数优先于内部 selectedKBId */
  async function startUpload(kbId?: number | null): Promise<boolean> {
    const targetKBId = kbId ?? selectedKBId.value;
    if (!file.value || targetKBId === null) {
      ElMessage.warning("请选择知识库和文件");
      return false;
    }

    uploading.value = true;
    progress.value = 0;
    uploaded.value = null;

    try {
      const progressTimer = setInterval(() => {
        if (progress.value < 90) progress.value += 10;
      }, 300);

      const result = await uploadDocument(file.value, targetKBId);

      clearInterval(progressTimer);
      progress.value = 100;
      uploaded.value = { filename: result.filename, chunks: result.chunks_count };
      ElMessage.success(`上传成功：${result.filename}，${result.chunks_count} 个片段已索引`);
      return true;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "上传失败";
      ElMessage.error(msg);
      return false;
    } finally {
      uploading.value = false;
    }
  }

  /** 重置状态 */
  function reset() {
    file.value = null;
    uploaded.value = null;
    progress.value = 0;
  }

  return {
    selectedKBId,
    file,
    uploading,
    progress,
    uploaded,
    onFileSelected,
    startUpload,
    reset,
  };
}
