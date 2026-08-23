/**
 * API 接口类型定义
 *
 * 与后端 app/api/schemas.py 一一对应。
 * 前端所有 API 调用统一使用此处定义的类型。
 */

// ============================================================
// POST /api/v1/chat
// ============================================================

/** 问答请求 */
export interface AskRequest {
  session_id: string;
  query: string;
  kb_id?: number;
}

/** 问答响应 */
export interface AskResponse {
  session_id: string;
  answer: string;
  citations: string[];
  intent: string;
}

// ============================================================
// 通用
// ============================================================

/** 错误响应 */
export interface ErrorResponse {
  detail: string;
  error_type: string;
}

// ============================================================
// GET /health
// ============================================================

/** 健康检查响应 */
export interface HealthResponse {
  status: string;
  app_name: string;
  version: string;
}
