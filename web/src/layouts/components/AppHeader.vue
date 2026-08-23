<script setup lang="ts">
/**
 * 顶部 Header 组件
 *
 * 包含：折叠按钮、面包屑、系统名称、用户菜单占位。
 * 深色模式 / 全屏按钮预留。
 */

import { Fold, Expand, UserFilled } from "@element-plus/icons-vue";
import { useLayoutStore } from "@/stores/layout";
import AppBreadcrumb from "./AppBreadcrumb.vue";

const layoutStore = useLayoutStore();
</script>

<template>
  <div class="app-header">
    <!-- 左侧：折叠 + 面包屑 -->
    <div class="header-left">
      <el-button text @click="layoutStore.toggleSidebar()" class="collapse-btn">
        <el-icon :size="20">
          <Fold v-if="!layoutStore.isSidebarCollapsed" />
          <Expand v-else />
        </el-icon>
      </el-button>
      <AppBreadcrumb />
    </div>

    <!-- 右侧：操作区 -->
    <div class="header-right">
      <!-- 深色模式切换（预留） -->
      <!-- 全屏按钮（预留） -->

      <!-- 用户菜单（占位） -->
      <el-dropdown trigger="click">
        <span class="user-area">
          <el-icon><UserFilled /></el-icon>
          <span class="user-name">管理员</span>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item disabled>个人设置（开发中）</el-dropdown-item>
            <el-dropdown-item disabled>退出登录（开发中）</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </div>
</template>

<style scoped>
.app-header {
  height: var(--header-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  background: var(--header-bg, #fff);
  border-bottom: 1px solid var(--border-color);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.collapse-btn {
  flex-shrink: 0;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user-area {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  font-size: 14px;
  color: var(--text-regular);
}

.user-name {
  user-select: none;
}
</style>
