/**
 * 会话状态管理
 *
 * Pinia Store + localStorage 持久化。
 * 后续接入后端 API 时只需替换读写目标，接口保持不变。
 */

import { ref, computed } from "vue";
import { defineStore } from "pinia";

const STORAGE_KEY = "graphrag_conversations";

export interface ConversationItem {
  id: string;
  title: string;
  updated_at: string;
}

function loadFromStorage(): ConversationItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

/** 从后端恢复会话列表（localStorage 丢失时使用） */
async function restoreFromBackend(): Promise<ConversationItem[]> {
  try {
    const { default: http } = await import("@/api/index");
    const resp = await http.get("/conversations");
    const data = resp.data as { conversations: ConversationItem[] };
    return data.conversations || [];
  } catch {
    return [];
  }
}

function saveToStorage(items: ConversationItem[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
}

export const useConversationStore = defineStore("conversation", () => {
  // --- 状态 ---
  const items = ref<ConversationItem[]>(loadFromStorage());
  const currentId = ref<string | null>(items.value[0]?.id ?? null);

  // --- 计算属性 ---
  const currentItem = computed(() =>
    items.value.find((c) => c.id === currentId.value) ?? null,
  );

  const isEmpty = computed(() => items.value.length === 0);

  /** 初始化：localStorage 为主，丢失时从后端恢复 */
  async function init() {
    if (items.value.length === 0) {
      const restored = await restoreFromBackend();
      if (restored.length > 0) {
        items.value = restored;
        currentId.value = restored[0].id;
        saveToStorage(items.value);
      }
    }
  }

  // --- 操作 ---
  function create(): ConversationItem {
    const conv: ConversationItem = {
      id: `sess_${crypto.randomUUID().slice(0, 8)}`,
      title: "新会话",
      updated_at: new Date().toISOString(),
    };
    items.value.unshift(conv);
    currentId.value = conv.id;
    saveToStorage(items.value);
    return conv;
  }

  function remove(id: string) {
    items.value = items.value.filter((c) => c.id !== id);
    if (currentId.value === id) {
      currentId.value = items.value[0]?.id ?? null;
    }
    saveToStorage(items.value);
  }

  function select(id: string) {
    currentId.value = id;
  }

  /** 更新会话标题（首条消息后调用） */
  function updateTitle(id: string, title: string) {
    const conv = items.value.find((c) => c.id === id);
    if (conv && conv.title === "新会话") {
      conv.title = title.length > 30 ? title.slice(0, 30) + "..." : title;
      conv.updated_at = new Date().toISOString();
      saveToStorage(items.value);
    }
  }

  /** 更新会话时间（每次发消息后调用） */
  function touch(id: string) {
    const conv = items.value.find((c) => c.id === id);
    if (conv) {
      conv.updated_at = new Date().toISOString();
      saveToStorage(items.value);
    }
  }

  return {
    items,
    currentId,
    currentItem,
    isEmpty,
    init,
    create,
    remove,
    select,
    updateTitle,
    touch,
  };
});
