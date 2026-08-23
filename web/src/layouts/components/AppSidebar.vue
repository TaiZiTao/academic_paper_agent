<script setup lang="ts">
/**
 * 侧边栏组件
 *
 * 菜单数据来自 @/router/menu.ts，组件本身不维护菜单项。
 * 折叠状态由 useLayoutStore 管理。
 */

import { useRoute } from "vue-router";
import { useLayoutStore } from "@/stores/layout";
import { menuItems } from "@/router/menu";
import { ChatDotRound, HomeFilled, Reading, Setting } from "@element-plus/icons-vue";

const route = useRoute();
const layoutStore = useLayoutStore();

const iconMap: Record<string, any> = {
  ChatDotRound, HomeFilled, Reading, Setting,
};
</script>

<template>
  <div class="app-sidebar">
    <!-- Logo 区域 -->
    <div class="sidebar-logo">
      <template v-if="!layoutStore.isSidebarCollapsed">
        <div class="logo-block">
          <span class="logo-text">论文智答</span>
          <span class="logo-sub">论文知识问答平台</span>
        </div>
      </template>
      <span v-else class="logo-text">G</span>
    </div>

    <!-- 菜单 -->
    <el-menu
      :collapse="layoutStore.isSidebarCollapsed"
      :default-active="route.path"
      router
      background-color="#304156"
      text-color="#bfcbd9"
      active-text-color="#409eff"
      class="sidebar-menu"
    >
      <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
        <el-icon>
          <component :is="iconMap[item.icon]" />
        </el-icon>
        <template #title>{{ item.title }}</template>
      </el-menu-item>
    </el-menu>
  </div>
</template>

<style scoped>
.app-sidebar {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--sidebar-bg);
  overflow: hidden;
}

.sidebar-logo {
  height: var(--header-height);
  display: flex;
  align-items: center;
  justify-content: center;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  flex-shrink: 0;
}

.logo-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  line-height: 1.2;
}

.logo-text {
  color: #fff;
  font-size: 18px;
  font-weight: bold;
  white-space: nowrap;
}

.logo-sub {
  color: rgba(255, 255, 255, 0.65);
  font-size: 12px;
  margin-top: 2px;
  white-space: nowrap;
}

.sidebar-menu {
  flex: 1;
  border-right: none;
  overflow-y: auto;
}
</style>
