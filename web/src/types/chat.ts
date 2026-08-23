/**
 * Chat 类型定义
 */

import type { PaperCitation } from "@/types/paper";

/** 角色类型 */
export type Role = "user" | "assistant";

/** 单条聊天消息 */
export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  citations?: PaperCitation[];
  intent?: string;
  timestamp: string;
}

/** 会话信息 */
export interface Conversation {
  id: string;
  title: string;
  updated_at: string;
}

/**
 * 生成唯一 ID
 */
export function generateId(): string {
  return crypto.randomUUID().slice(0, 8);
}
