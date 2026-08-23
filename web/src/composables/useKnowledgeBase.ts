/**
 * 知识库管理 Composable
 *
 * 负责：列表加载、CRUD、搜索、分页、Dialog 状态。
 * 页面只负责展示，不直接调用 API。
 */

import { ref, reactive } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import type { KnowledgeBase, CreateKBRequest, UpdateKBRequest } from "@/types/kb";
import {
  listKnowledgeBases,
  createKnowledgeBase,
  updateKnowledgeBase,
  deleteKnowledgeBase,
} from "@/api/kb";

export function useKnowledgeBase() {
  // --- 列表状态 ---
  const items = ref<KnowledgeBase[]>([]);
  const total = ref(0);
  const loading = ref(false);
  const error = ref("");

  // --- 搜索 & 分页 ---
  const search = ref("");
  const page = ref(1);
  const pageSize = ref(10);

  // --- Dialog 状态 ---
  const dialogVisible = ref(false);
  const dialogTitle = ref("新建知识库");
  const editingItem = ref<KnowledgeBase | null>(null);

  /** 表单数据（v-model 绑定） */
  const form = reactive<CreateKBRequest>({
    name: "",
    description: "",
  });

  /** 加载列表 */
  async function fetchList() {
    loading.value = true;
    error.value = "";
    try {
      const res = await listKnowledgeBases({
        search: search.value,
        page: page.value,
        page_size: pageSize.value,
      });
      items.value = res.items;
      total.value = res.total;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "加载失败";
      error.value = msg;
    } finally {
      loading.value = false;
    }
  }

  /** 搜索（重置到第 1 页） */
  function onSearch() {
    page.value = 1;
    fetchList();
  }

  /** 页码变化 */
  function onPageChange(p: number) {
    page.value = p;
    fetchList();
  }

  /** 每页条数变化 */
  function onPageSizeChange(size: number) {
    pageSize.value = size;
    page.value = 1;
    fetchList();
  }

  /** 打开新建 Dialog */
  function openCreateDialog() {
    dialogTitle.value = "新建知识库";
    editingItem.value = null;
    form.name = "";
    form.description = "";
    dialogVisible.value = true;
  }

  /** 打开编辑 Dialog */
  function openEditDialog(item: KnowledgeBase) {
    dialogTitle.value = "编辑知识库";
    editingItem.value = item;
    form.name = item.name;
    form.description = item.description;
    dialogVisible.value = true;
  }

  /** 提交表单（新建或编辑） */
  async function submitForm() {
    if (!form.name.trim()) {
      ElMessage.warning("请输入知识库名称");
      return;
    }

    loading.value = true;
    try {
      if (editingItem.value) {
        const data: UpdateKBRequest = {
          name: form.name,
          description: form.description,
        };
        await updateKnowledgeBase(editingItem.value.id, data);
        ElMessage.success("编辑成功");
      } else {
        await createKnowledgeBase(form);
        ElMessage.success("创建成功");
      }
      dialogVisible.value = false;
      await fetchList();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "操作失败";
      ElMessage.error(msg);
    } finally {
      loading.value = false;
    }
  }

  /** 删除知识库 */
  async function handleDelete(item: KnowledgeBase) {
    try {
      await ElMessageBox.confirm(
        `确定要删除知识库「${item.name}」吗？如有文档请先删除文档。`,
        "删除确认",
        { confirmButtonText: "确定", cancelButtonText: "取消", type: "warning" },
      );
      await deleteKnowledgeBase(item.id);
      ElMessage.success("删除成功");
      if (items.value.length === 1 && page.value > 1) {
        page.value -= 1;
      }
      await fetchList();
    } catch (e: unknown) {
      if (e !== "cancel" && e !== "close") {
        const msg = (e as any)?.response?.data?.detail || (e instanceof Error ? e.message : "删除失败");
        ElMessage.error(msg);
      }
    }
  }

  return {
    // 列表
    items,
    total,
    loading,
    error,
    // 搜索 & 分页
    search,
    page,
    pageSize,
    // Dialog
    dialogVisible,
    dialogTitle,
    editingItem,
    form,
    // 方法
    fetchList,
    onSearch,
    onPageChange,
    onPageSizeChange,
    openCreateDialog,
    openEditDialog,
    submitForm,
    handleDelete,
  };
}
