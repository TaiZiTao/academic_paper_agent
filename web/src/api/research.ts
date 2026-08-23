import http from "./index";
import type {
  BrowserStatus,
  ImportTask,
  ResearchProgressEvent,
  SearchResult,
} from "@/types/research";

export interface ResearchSearchCallbacks {
  onPlan?: (queries: string[], sources: string[], direct?: boolean) => void;
  /** offset 为服务端回显值; undefined 表示服务端未回显, 调用方应保持现状 */
  onResults?: (results: SearchResult[], total: number, offset: number | undefined, totalIsEstimate?: boolean) => void;
  onDone?: () => void;
  onError?: (message: string) => void;
}

export interface ResearchSearchOptions {
  /** 起始年份筛选, null/缺省 = 不限 */
  yearMin?: number | null;
  /** 结束年份筛选, null/缺省 = 不限 */
  yearMax?: number | null;
  /** 强制重新检索(搜索/搜索新结果传 true; 翻页不传 → 后端走结果集缓存切片) */
  refresh?: boolean;
  signal?: AbortSignal;
}

export async function searchResearch(
  query: string,
  topK: number,
  offset: number = 0,
  options: ResearchSearchOptions = {},
  callbacks?: ResearchSearchCallbacks,
): Promise<void> {
  const body: Record<string, unknown> = { query, top_k: topK, offset };
  if (options.yearMin != null) body.year_min = options.yearMin;
  if (options.yearMax != null) body.year_max = options.yearMax;
  if (options.refresh) body.refresh = true;
  const response = await fetch("/api/v1/research/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: options.signal,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "HTTP " + response.status);
  }
  if (!response.body) throw new Error("服务端没有返回流式内容");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const lines = part.split("\n");
      const eventLine = lines.find((l) => l.startsWith("event:"));
      const dataLine = lines.find((l) => l.startsWith("data:"));
      if (!eventLine || !dataLine) continue;
      const eventName = eventLine.slice(6).trim() as ResearchProgressEvent["event"];
      const data = JSON.parse(dataLine.slice(5).trim() || "{}");
      if (eventName === "plan") {
        callbacks?.onPlan?.(data.queries || [], data.sources || [], data.direct);
      } else if (eventName === "results") {
        callbacks?.onResults?.(
          data.items || [],
          data.total || 0,
          // 服务端回显的 offset 原样透传; undefined 表示未回显, 调用方保持现状即可
          data.offset,
          data.total_is_estimate,
        );
      } else if (eventName === "error") {
        callbacks?.onError?.(data.message || "搜索失败");
      } else if (eventName === "done") {
        callbacks?.onDone?.();
      }
    }
  }
  callbacks?.onDone?.();
}

export async function createImports(items: Array<{
  source: string;
  title: string;
  year?: number | null;
  venue?: string;
  doi?: string | null;
  pdf_url?: string | null;
  page_url?: string | null;
  external_id?: string | null;
}>): Promise<ImportTask[]> {
  const response = await http.post<{ items: ImportTask[] }>("/research/imports", { items });
  return response.data.items;
}

export async function listImports(): Promise<ImportTask[]> {
  const response = await http.get<{ items: ImportTask[] }>("/research/imports");
  return response.data.items;
}

export async function retryImport(importId: number): Promise<ImportTask> {
  const response = await http.post<ImportTask>("/research/imports/" + importId + "/retry");
  return response.data;
}

export async function getBrowserStatus(): Promise<BrowserStatus> {
  const response = await http.get<BrowserStatus>("/research/browser/status");
  return response.data;
}

export async function browserLogin(): Promise<BrowserStatus> {
  const response = await http.post<BrowserStatus>("/research/browser/login");
  return response.data;
}

export async function browserVerify(): Promise<BrowserStatus> {
  const response = await http.post<BrowserStatus>("/research/browser/verify");
  return response.data;
}

export async function browserClose(): Promise<{ status: string }> {
  const response = await http.post<{ status: string }>("/research/browser/close");
  return response.data;
}
