import http from "./index";
import type {
  PaperCitation,
  PaperDetail,
  PaperListResponse,
  PaperProgressEvent,
  PaperSummary,
  PaperTaskCallbacks,
  PaperTaskDoneEvent,
  PaperTaskRequest,
} from "@/types/paper";

export async function uploadPaper(file: File): Promise<PaperSummary> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await http.post<PaperSummary>("/papers", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120_000,
  });
  return response.data;
}

export async function listPapers(params: {
  search?: string;
  page?: number;
  page_size?: number;
  field?: string;
  group?: boolean;
} = {}): Promise<PaperListResponse> {
  const response = await http.get<PaperListResponse>("/papers", { params });
  return response.data;
}

export async function updatePaperField(paperId: number, field: string): Promise<PaperSummary> {
  const response = await http.put<PaperSummary>(`/papers/${paperId}/field`, { field });
  return response.data;
}

export async function getPaperDetail(paperId: number): Promise<PaperDetail> {
  const response = await http.get<PaperDetail>(`/papers/${paperId}`);
  return response.data;
}

export async function retryPaper(paperId: number): Promise<void> {
  await http.post(`/papers/${paperId}/retry`);
}

export async function deletePaper(paperId: number): Promise<void> {
  await http.delete(`/papers/${paperId}`);
}

export function paperPdfUrl(paperId: number): string {
  return `/api/v1/papers/${paperId}/pdf`;
}


export function figureImageUrl(paperId: number, figureId: number): string {
  return `/api/v1/papers/${paperId}/figures/${figureId}/image`;
}

export function listenPaperProgress(
  paperId: number,
  onEvent: (event: PaperProgressEvent) => void,
  onError?: () => void,
): () => void {
  const source = new EventSource(`/api/v1/papers/${paperId}/events`);
  const eventNames = ["progress", "done", "error"] as const;
  for (const eventName of eventNames) {
    source.addEventListener(eventName, (raw) => {
      const message = raw as MessageEvent<string>;
      const data = JSON.parse(message.data || "{}") as Omit<PaperProgressEvent, "event">;
      onEvent({ event: eventName, ...data });
      if (eventName === "done" || eventName === "error") source.close();
    });
  }
  source.onerror = () => {
    source.close();
    onError?.();
  };
  return () => source.close();
}

export async function streamPaperTask(
  paperId: number,
  request: PaperTaskRequest,
  callbacks: PaperTaskCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/v1/papers/${paperId}/tasks/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `HTTP ${response.status}`);
  }
  if (!response.body) throw new Error("服务端没有返回流式内容");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) dispatchSseBlock(block, callbacks);
  }
  if (buffer.trim()) dispatchSseBlock(buffer, callbacks);
}

function dispatchSseBlock(block: string, callbacks: PaperTaskCallbacks): void {
  let eventName = "message";
  let data = "{}";
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) eventName = line.slice(7).trim();
    if (line.startsWith("data: ")) data = line.slice(6);
  }
  const payload = JSON.parse(data) as Record<string, unknown>;
  if (eventName === "progress") callbacks.onProgress?.(payload);
  if (eventName === "token") callbacks.onToken?.(String(payload.content || ""));
  if (eventName === "block") callbacks.onBlock?.(payload as unknown as import("@/types/paper").PaperTranslationBlock);
  if (eventName === "figure") callbacks.onFigure?.(payload as unknown as import("@/types/paper").PaperFigure);
  if (eventName === "done") callbacks.onDone?.(payload as unknown as PaperTaskDoneEvent);
  if (eventName === "error") callbacks.onError?.(String(payload.detail || "任务失败"));
}

/** LangGraph 节点事件(后端 node 事件 payload) */
export interface LibraryQaNodeEvent {
  node: string;
  status: string;
  [k: string]: unknown;
}

/** 全库问答流式接口(POST /api/v1/papers/qa/stream) */
export function streamLibraryQA(
  inputText: string,
  sessionId: string,
  callbacks: {
    onToken: (t: string) => void;
    onDone: (d: { content: string; citations: PaperCitation[] }) => void;
    onError: (e: string) => void;
    onFilter?: (f: { filters: Record<string, unknown>; candidates: number; degraded: string[] }) => void;
    onNode?: (n: LibraryQaNodeEvent) => void;
  },
  signal?: AbortSignal,
): void {
  fetch(`/api/v1/papers/qa/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input_text: inputText, session_id: sessionId }),
    signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(payload.detail || `HTTP ${res.status}`);
      }
      const reader = res.body?.getReader();
      if (!reader) throw new Error("服务端没有返回流式内容");

      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        let value: Uint8Array | undefined;
        try {
          const read = await reader.read();
          if (read.done) break;
          value = read.value;
        } catch (e) {
          // 主动取消静默返回; 其余错误走 onError 结束流
          if ((e as Error)?.name === "AbortError") return;
          callbacks.onError(String((e as Error)?.message || "问答失败"));
          return;
        }
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";
        for (const block of blocks) dispatchLibraryQaBlock(block, callbacks);
      }
      // 截断流时 flush 尾块, 不丢最后一个事件
      if (buffer.trim()) dispatchLibraryQaBlock(buffer, callbacks);
    })
    .catch((e) => {
      // 主动取消(切换会话/卸载)不算失败
      if ((e as Error)?.name === "AbortError") return;
      callbacks.onError(String(e.message || "问答失败"));
    });
}

function dispatchLibraryQaBlock(
  block: string,
  callbacks: {
    onToken: (t: string) => void;
    onDone: (d: { content: string; citations: PaperCitation[] }) => void;
    onError: (e: string) => void;
    onFilter?: (f: { filters: Record<string, unknown>; candidates: number; degraded: string[] }) => void;
    onNode?: (n: LibraryQaNodeEvent) => void;
  },
): void {
  let eventName = "message";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) eventName = line.slice(7).trim();
    if (line.startsWith("data: ")) data = line.slice(6);
  }
  if (!data) return;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data) as Record<string, unknown>;
  } catch {
    return; // 畸形块跳过
  }
  if (eventName === "filter") {
    callbacks.onFilter?.(payload as unknown as { filters: Record<string, unknown>; candidates: number; degraded: string[] });
  } else if (eventName === "node") {
    callbacks.onNode?.(payload as unknown as LibraryQaNodeEvent);
  } else if (eventName === "token") callbacks.onToken(String(payload.content || ""));
  else if (eventName === "done") {
    callbacks.onDone(payload as unknown as { content: string; citations: PaperCitation[] });
  } else if (eventName === "error") {
    callbacks.onError(String(payload.detail || "问答失败"));
  }
}

/** 全库问答会话历史(GET /api/v1/papers/qa/history) */
export async function getLibraryHistory(
  sessionId: string,
): Promise<{
  session_id: string;
  messages: { role: string; content: string; citations: PaperCitation[]; timestamp: string }[];
}> {
  const response = await http.get("/papers/qa/history", { params: { session_id: sessionId } });
  return response.data;
}
