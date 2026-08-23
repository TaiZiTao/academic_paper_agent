/**
 * Chat Composable — 全库问答(论文库)流式
 *
 * - sendMessage 走 streamLibraryQA(POST /api/v1/papers/qa/stream)
 * - loadHistory 读取全库会话历史(paper_id=0 的 paper_messages)
 */

import { reactive, ref, nextTick, watch } from "vue";
import { ElMessage } from "element-plus";
import { getLibraryHistory, streamLibraryQA } from "@/api/paper";
import type { LibraryQaNodeEvent } from "@/api/paper";
import type { PaperCitation } from "@/types/paper";
import type { ChatMessage } from "@/types/chat";
import { generateId } from "@/types/chat";

/** 工作流节点状态(pending → running → completed) */
export interface WorkflowNodeState {
  node: string;
  status: "pending" | "running" | "completed";
  extra?: Record<string, unknown>;
}

export function useChat(externalSessionId?: () => string | null) {
  const messages = ref<ChatMessage[]>([]);
  const loading = ref(false);
  const error = ref("");
  const sessionId = ref(externalSessionId?.() ?? generateSessionId());

  const citations = ref<PaperCitation[]>([]);
  const workflowNodes = ref<WorkflowNodeState[]>([]);
  const isStreaming = ref(false);
  let abortController: AbortController | null = null;

  if (externalSessionId) {
    watch(externalSessionId, (newId) => {
      if (newId && newId !== sessionId.value) {
        sessionId.value = newId;
        messages.value = [];
        error.value = "";
        citations.value = [];
        workflowNodes.value = [];
        abort();
      }
    });
  }

  const listRef = ref<{ scrollToBottom: () => void } | null>(null);

  async function sendMessage(query: string): Promise<void> {
    const trimmed = query.trim();
    if (!trimmed || loading.value) return;

    const userMsg: ChatMessage = {
      id: generateId(), role: "user", content: trimmed,
      timestamp: new Date().toISOString(),
    };
    messages.value.push(userMsg);
    loading.value = true;
    isStreaming.value = true;
    error.value = "";
    citations.value = [];
    // 新一轮问答: 清空上次的节点状态, 让 WorkflowPanel 从空开始重新点亮
    workflowNodes.value = [];

    // 必须用 reactive: 直接改 asstMsg.content 要能触发列表重新渲染
    // (普通对象 push 进 ref 数组后, 直改原对象不会触发响应式更新)
    const asstMsg: ChatMessage = reactive({
      id: generateId(), role: "assistant", content: "",
      timestamp: new Date().toISOString(),
    });
    messages.value.push(asstMsg);

    await nextTick();
    listRef.value?.scrollToBottom();

    abortController = new AbortController();
    const signal = AbortSignal.any([abortController.signal, AbortSignal.timeout(300_000)]);

    streamLibraryQA(
      trimmed,
      sessionId.value,
      {
        // 直接操作 asstMsg 对象引用, 不依赖数组索引(会话切换后索引会错位)
        onToken(content) {
          asstMsg.content += content;
          listRef.value?.scrollToBottom();
        },
        onFilter({ degraded, candidates }) {
          // 兼容旧事件: 后端图驱动后已不发 filter 事件, 保留兜底
          if (degraded && degraded.length > 0) {
            const note = `\n\n> 注: 未找到完全符合过滤条件的论文, 已${degraded.includes("all") ? "按全部论文" : "放宽年份等条件"}回答(共检索 ${candidates} 篇)。`;
            asstMsg.content += note;
          }
        },
        onNode(n) {
          upsertNode(n);
          // 方向选择节点带降级标记(候选论文被放宽)时附注, 避免用户误以为按原条件检索
          if (n.node === "direction_select" && Array.isArray(n.degraded) && n.degraded.length > 0) {
            const degraded = n.degraded as string[];
            const note = `\n\n> 注: 未找到完全符合过滤条件的论文, 已${degraded.includes("all") ? "按全部论文" : "放宽年份等条件"}回答。`;
            if (!asstMsg.content.includes("未找到完全符合过滤条件的论文")) {
              asstMsg.content += note;
            }
          }
        },
        onDone(event) {
          asstMsg.content = event.content || asstMsg.content;
          asstMsg.citations = event.citations;
          citations.value = event.citations || [];
          finish();
        },
        onError(message) {
          error.value = message;
          ElMessage.error(message);
          if (!asstMsg.content) {
            const index = messages.value.indexOf(asstMsg);
            if (index !== -1) messages.value.splice(index, 1);
          } else {
            asstMsg.content += "\n\n*[回答中断]*";
          }
          finish();
        },
      },
      signal,
    );
  }

  function finish() {
    loading.value = false;
    isStreaming.value = false;
    abortController = null;
  }

  /** 按节点名去重更新状态(pending → running → completed), 重试节点多次出现时只保留最新状态 */
  function upsertNode(n: LibraryQaNodeEvent) {
    const status =
      n.status === "pending" || n.status === "running" || n.status === "completed"
        ? n.status
        : "completed";
    const existing = workflowNodes.value.find((w) => w.node === n.node);
    if (existing) {
      existing.status = status;
      const { node: _node, status: _status, ...rest } = n;
      if (Object.keys(rest).length) existing.extra = rest as Record<string, unknown>;
    } else {
      const { node, status: _s, ...rest } = n;
      workflowNodes.value.push({ node, status, extra: rest as Record<string, unknown> });
    }
  }

  function abort() {
    abortController?.abort();
    abortController = null;
    loading.value = false;
    isStreaming.value = false;
  }

  /** 从后端加载全库问答会话历史 */
  async function loadHistory(sid: string) {
    messages.value = [];
    error.value = "";
    try {
      const res = await getLibraryHistory(sid);
      messages.value = res.messages.map((m) => ({
        id: generateId(),
        role: m.role as ChatMessage["role"],
        content: m.content,
        citations: m.citations || [],
        timestamp: m.timestamp,
      }));
      await nextTick();
      listRef.value?.scrollToBottom();
    } catch {
      // 历史加载失败不阻塞，当作空会话
    }
  }

  return {
    messages, loading, error, sessionId,
    listRef, sendMessage, abort, loadHistory,
    citations, isStreaming, workflowNodes,
  };
}

function generateSessionId(): string {
  return `sess_${crypto.randomUUID().slice(0, 8)}`;
}
