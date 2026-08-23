/**
 * 问答 API
 *
 * 封装 POST /api/v1/chat 接口调用。
 */

import http from "./index";
import type { AskRequest, AskResponse } from "@/types/api";

/**
 * 发送知识问答请求
 */
export async function askChat(data: AskRequest): Promise<AskResponse> {
  const response = await http.post<AskResponse>("/chat", data);
  return response.data;
}

/** 获取会话历史消息 */
export async function getHistory(sessionId: string): Promise<{
  session_id: string;
  messages: { role: string; content: string; citations?: string[]; timestamp: string }[];
}> {
  const response = await http.get(`/conversations/${sessionId}/messages`);
  return response.data;
}
