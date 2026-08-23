/**
 * 会话管理 Composable
 *
 * 封装 Pinia Store，使用 storeToRefs 保持响应性。
 */

import { useConversationStore } from "@/stores/conversation";
import { storeToRefs } from "pinia";

export function useConversation() {
  const store = useConversationStore();
  const { items, currentId, currentItem, isEmpty } = storeToRefs(store);

  return {
    items,
    currentId,
    currentItem,
    isEmpty,
    init: store.init,
    // actions 不需要 storeToRefs
    create: store.create,
    remove: store.remove,
    select: store.select,
    updateTitle: store.updateTitle,
    touch: store.touch,
  };
}
