<script setup lang="ts">
/**
 * 主布局 — 纯编排器
 *
 * 只负责三栏布局壳子，具体逻辑委托给子组件：
 *   AppHeader   — 顶部栏
 *   AppSidebar  — 侧边栏
 *   <router-view> — 内容区
 *
 * 不包含任何 Header/Sidebar 内部实现细节。
 */

import { useLayoutStore } from "@/stores/layout";
import AppHeader from "./components/AppHeader.vue";
import AppSidebar from "./components/AppSidebar.vue";

const layoutStore = useLayoutStore();
</script>

<template>
  <el-container class="main-layout">
    <!-- 侧边栏 -->
    <el-aside :width="layoutStore.isSidebarCollapsed ? '64px' : '220px'" class="main-aside">
      <AppSidebar />
    </el-aside>

    <!-- 右侧：Header + 内容 -->
    <el-container>
      <el-header class="main-header">
        <AppHeader />
      </el-header>

      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.main-layout {
  height: 100vh;
}

.main-aside {
  background-color: var(--sidebar-bg);
  transition: width 0.3s;
}

.main-header {
  padding: 0;
  height: var(--header-height);
}

.main-content {
  background: var(--content-bg);
  padding: 0;
  /* 内容区垂直滚动: 论文库等流式页面内容超出视口时可下滑; Chat/Workspace 自带内部滚动, 不受影响 */
  overflow-x: hidden;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  flex: 1;
}
</style>
