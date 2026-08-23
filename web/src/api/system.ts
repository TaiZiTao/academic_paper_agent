/**
 * 系统 API
 */

import type { HealthStatus } from "@/types/system";

/** 获取服务健康状态 */
export async function getHealthStatus(): Promise<HealthStatus> {
  // /health 不走 /api/v1 前缀，通过 Vite proxy 转发
  const response = await fetch("/health");
  if (!response.ok) throw new Error(`Health check failed: ${response.status}`);
  return response.json();
}
