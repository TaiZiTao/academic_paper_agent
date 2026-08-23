/**
 * 权限相关类型 — Phase 15 预留
 *
 * 当前仅定义类型结构，不实现完整 RBAC。
 */

/** 用户信息 */
export interface User {
  id: string;
  username: string;
  role: UserRole;
}

/** 用户角色 */
export type UserRole = "admin" | "editor" | "viewer";

/** 权限点 */
export type Permission = "kb:read" | "kb:write" | "doc:upload" | "doc:delete" | "chat:ask";
