/**
 * 文档列表 Composable
 *
 * 负责：列表加载、分页、搜索、删除、KB 筛选。
 */

import { ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { listDocuments, deleteDocument } from "@/api/document";
import type { DocumentInfo } from "@/types/document";

export function useDocumentList() {
  const items = ref<DocumentInfo[]>([]);
  const total = ref(0);
  const loading = ref(false);
  const error = ref("");

  const search = ref("");
  const filterKBId = ref<number | null>(null);
  const page = ref(1);
  const pageSize = ref(10);

  /** KB 筛选变化时自动重新加载 */
  watch(filterKBId, () => {
    page.value = 1;
    fetchList();
  });

  async function fetchList() {
    loading.value = true;
    error.value = "";
    try {
      const res = await listDocuments({
        kb_id: filterKBId.value,
        search: search.value || undefined,
        page: page.value,
        page_size: pageSize.value,
      });
      items.value = res.items;
      total.value = res.total;
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : "加载失败";
    } finally {
      loading.value = false;
    }
  }

  function onSearch() {
    page.value = 1;
    fetchList();
  }

  function onPageChange(p: number) {
    page.value = p;
    fetchList();
  }

  function onPageSizeChange(s: number) {
    pageSize.value = s;
    page.value = 1;
    fetchList();
  }

  /** KB 筛选变化时重新加载（已通过 watch 自动触发，保留显式方法供手动调用） */
  function onKBFilterChange() {
    page.value = 1;
    fetchList();
  }

  /** 上传成功后刷新列表 */
  function onUploadSuccess() {
    page.value = 1;
    fetchList();
  }

  async function handleDelete(item: DocumentInfo) {
    try {
      await ElMessageBox.confirm(
        `确定要删除文档「${item.original_filename}」吗？`,
        "删除确认",
        { confirmButtonText: "确定", cancelButtonText: "取消", type: "warning" },
      );
      await deleteDocument(item.id);
      ElMessage.success("删除成功");
      if (items.value.length === 1 && page.value > 1) {
        page.value -= 1;
      }
      await fetchList();
    } catch (e: unknown) {
      if (e !== "cancel" && e !== "close") {
        ElMessage.error(e instanceof Error ? e.message : "删除失败");
      }
    }
  }

  /** 格式化文件大小 */
  function formatSize(bytes: number): string {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
  }

  return {
    items, total, loading, error,
    search, filterKBId, page, pageSize,
    fetchList, onSearch, onPageChange, onPageSizeChange,
    onKBFilterChange, onUploadSuccess, handleDelete, formatSize,
  };
}
