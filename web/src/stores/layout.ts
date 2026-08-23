/**
 * Layout 状态管理
 *
 * 管理侧边栏折叠、当前激活菜单等纯布局状态。
 * 不包含任何业务状态。
 */

import { ref } from "vue";
import { defineStore } from "pinia";

export const useLayoutStore = defineStore("layout", () => {
  // --- 侧边栏 ---
  const isSidebarCollapsed = ref(false);

  function toggleSidebar() {
    isSidebarCollapsed.value = !isSidebarCollapsed.value;
  }

  // --- 深色模式（预留） ---
  const isDarkMode = ref(false);

  function toggleDarkMode() {
    isDarkMode.value = !isDarkMode.value;
  }

  // --- 全屏（预留） ---
  const isFullscreen = ref(false);

  return {
    isSidebarCollapsed,
    toggleSidebar,
    isDarkMode,
    toggleDarkMode,
    isFullscreen,
  };
});
