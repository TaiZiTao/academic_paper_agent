/**
 * Dark Mode Store — Phase 15 预留
 *
 * 切换通过 html[data-theme] 实现，不影响现有组件。
 */

import { ref, watch } from "vue";
import { defineStore } from "pinia";

export const useThemeStore = defineStore("theme", () => {
  const isDark = ref(false);

  function toggle() {
    isDark.value = !isDark.value;
  }

  // 同步到 DOM
  watch(isDark, (val) => {
    document.documentElement.setAttribute("data-theme", val ? "dark" : "light");
  }, { immediate: true });

  return { isDark, toggle };
});
