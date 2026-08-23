/**
 * 系统设置相关类型
 */

/** 服务状态（来自 GET /health） */
export interface HealthStatus {
  status: string;
  app_name: string;
  version: string;
}

/** 模型配置（只读展示） */
export interface ModelConfig {
  llm_provider: string;
  llm_model: string;
  embedding_model: string;
}

/** RAG 配置（只读展示） */
export interface RagConfig {
  chunk_size: number;
  chunk_overlap: number;
  top_k: number;
  vector_weight: number;
  keyword_weight: number;
}
