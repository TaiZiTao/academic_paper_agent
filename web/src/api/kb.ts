/**
 * 知识库 API
 *
 * 封装 KnowledgeBase CRUD 接口调用。
 */

import http from "./index";
import type {
  CreateKBRequest,
  KnowledgeBase,
  ListKBParams,
  ListKBResponse,
  UpdateKBRequest,
} from "@/types/kb";

/** 获取知识库列表 */
export async function listKnowledgeBases(params: ListKBParams = {}): Promise<ListKBResponse> {
  const response = await http.get<ListKBResponse>("/kb", { params });
  return response.data;
}

/** 创建知识库 */
export async function createKnowledgeBase(data: CreateKBRequest): Promise<KnowledgeBase> {
  const response = await http.post<KnowledgeBase>("/kb", data);
  return response.data;
}

/** 编辑知识库 */
export async function updateKnowledgeBase(
  id: number,
  data: UpdateKBRequest,
): Promise<KnowledgeBase> {
  const response = await http.put<KnowledgeBase>(`/kb/${id}`, data);
  return response.data;
}

/** 删除知识库 */
export async function deleteKnowledgeBase(id: number): Promise<void> {
  await http.delete(`/kb/${id}`);
}
