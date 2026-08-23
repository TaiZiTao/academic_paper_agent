/**
 * Axios 实例 + 请求/响应拦截器
 *
 * 所有 API 模块通过此实例发送请求，
 * 统一处理 baseURL、错误、token 注入。
 */

import axios from "axios";
import { ElMessage } from "element-plus";
import type { AxiosInstance, AxiosError, InternalAxiosRequestConfig } from "axios";

// 创建 Axios 实例
const http: AxiosInstance = axios.create({
  baseURL: "/api/v1",
  timeout: 300000,
  headers: {
    "Content-Type": "application/json",
  },
});

// --- 请求拦截器 ---
http.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    // Phase 14+: 在此注入 token
    // const token = localStorage.getItem("token");
    // if (token) {
    //   config.headers.Authorization = `Bearer ${token}`;
    // }
    return config;
  },
  (error: AxiosError) => {
    return Promise.reject(error);
  },
);

// --- 响应拦截器 ---
http.interceptors.response.use(
  (response) => {
    return response;
  },
  (error: AxiosError) => {
    const status = error.response?.status || 0;

    // 跳过业务错误（400-499），由各 composable 自行处理提示
    if (status >= 500 || status === 0) {
      const message =
        (error.response?.data as { detail?: string })?.detail ||
        error.message ||
        "网络请求失败";
      ElMessage.error(message);
    }

    return Promise.reject(error);
  },
);

export default http;
