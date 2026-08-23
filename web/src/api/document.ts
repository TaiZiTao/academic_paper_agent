/**
 * 文档 API
 */

import http from "./index";
import type {
  DocumentInfo,
  DocumentListParams,
  DocumentListResponse,
  DocumentUploadResponse,
} from "@/types/document";

/** 上传文档 */
export async function uploadDocument(
  file: File,
  kbId: number,
): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("kb_id", String(kbId));

  const response = await http.post<DocumentUploadResponse>(
    "/documents/upload",
    formData,
    {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 3600000,
    },
  );
  return response.data;
}

/** 获取文档列表 */
export async function listDocuments(
  params: DocumentListParams = {},
): Promise<DocumentListResponse> {
  const response = await http.get<DocumentListResponse>("/documents", { params });
  return response.data;
}

/** 获取单个文档 */
export async function getDocument(id: number): Promise<DocumentInfo> {
  const response = await http.get<DocumentInfo>(`/documents/${id}`);
  return response.data;
}

/** 删除文档 */
export async function deleteDocument(id: number): Promise<void> {
  await http.delete(`/documents/${id}`);
}
