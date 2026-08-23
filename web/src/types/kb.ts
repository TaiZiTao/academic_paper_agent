/**
 * KnowledgeBase 类型定义
 *
 * 与后端 app/api/schemas.py 的 KB Schema 一一对应。
 */

/** 知识库实体 */
export interface KnowledgeBase {
  id: number;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
}

/** 创建知识库请求 */
export interface CreateKBRequest {
  name: string;
  description?: string;
}

/** 编辑知识库请求（部分更新） */
export interface UpdateKBRequest {
  name?: string;
  description?: string;
}

/** 知识库列表响应 */
export interface ListKBResponse {
  items: KnowledgeBase[];
  total: number;
}

/** 列表查询参数 */
export interface ListKBParams {
  search?: string;
  page?: number;
  page_size?: number;
}
