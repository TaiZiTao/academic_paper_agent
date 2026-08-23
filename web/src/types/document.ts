/**
 * Document 类型定义
 */

// ============================================================
// 上传
// ============================================================

/** 上传文档响应 */
export interface DocumentUploadResponse {
  id: number;
  filename: string;
  chunks_count: number;
  kb_id: number;
}

/** 上传参数 */
export interface UploadDocumentParams {
  file: File;
  kb_id: number;
}

/** 允许的文件扩展名 */
export const ALLOWED_EXTENSIONS = [".txt", ".md", ".pdf", ".html", ".htm"];

/** 最大文件大小：10MB */
export const MAX_FILE_SIZE = 128 * 1024 * 1024;

// ============================================================
// 文档列表 (Phase 13C)
// ============================================================

/** 文档列表项 */
export interface DocumentInfo {
  id: number;
  kb_id: number;
  kb_name: string;
  original_filename: string;
  extension: string;
  size: number;
  chunk_count: number;
  status: string;
  created_at: string;
}

/** 文档列表响应 */
export interface DocumentListResponse {
  items: DocumentInfo[];
  total: number;
}

/** 列表查询参数 */
export interface DocumentListParams {
  kb_id?: number | null;
  search?: string;
  page?: number;
  page_size?: number;
}
