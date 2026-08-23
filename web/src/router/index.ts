import { createRouter, createWebHistory } from "vue-router";
import type { RouteRecordRaw } from "vue-router";
import MainLayout from "@/layouts/MainLayout.vue";

const routes: RouteRecordRaw[] = [
  {
    path: "/",
    component: MainLayout,
    redirect: "/home",
    children: [
      {
        path: "home",
        name: "Home",
        component: () => import("@/views/HomeView.vue"),
        meta: { title: "首页" },
      },
      {
        path: "kb",
        name: "KnowledgeBase",
        component: () => import("@/views/KnowledgeBaseView.vue"),
        meta: { title: "知识库管理" },
      },
      {
        path: "document",
        name: "Document",
        component: () => import("@/views/DocumentView.vue"),
        meta: { title: "文档管理" },
      },
      {
        path: "chat",
        name: "Chat",
        component: () => import("@/views/ChatView.vue"),
        meta: { title: "智能问答" },
      },
      {
        path: "papers",
        name: "Papers",
        component: () => import("@/views/PaperListView.vue"),
        meta: { title: "论文助手" },
      },
      {
        path: "papers/:paperId",
        name: "PaperWorkspace",
        component: () => import("@/views/PaperWorkspaceView.vue"),
        meta: { title: "论文精读" },
      },
      {
        path: "settings",
        name: "Settings",
        component: () => import("@/views/SettingsView.vue"),
        meta: { title: "系统设置" },
      },
    ],
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// --- 路由守卫 ---
router.beforeEach((to, _from, next) => {
  // 更新页面标题
  const title = to.meta.title as string | undefined;
  document.title = title ? `${title} — 论文智答` : "论文智答 · 论文知识问答平台";
  // Phase 14+: 在此添加权限校验
  next();
});

export default router;
