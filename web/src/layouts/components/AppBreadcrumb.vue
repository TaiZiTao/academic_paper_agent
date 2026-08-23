<script setup lang="ts">
/**
 * 面包屑组件
 *
 * 根据当前 route.matched 自动生成面包屑路径。
 * 过滤掉无 title 的 route，适配动态路由场景。
 */

import { computed } from "vue";
import { useRouter } from "vue-router";

const router = useRouter();

const breadcrumbs = computed(() => {
  return router.currentRoute.value.matched
    .filter((r) => r.meta?.title)
    .map((r) => ({
      title: r.meta.title as string,
      path: r.path,
    }));
});
</script>

<template>
  <el-breadcrumb separator="/">
    <el-breadcrumb-item v-for="item in breadcrumbs" :key="item.path" :to="{ path: item.path }">
      {{ item.title }}
    </el-breadcrumb-item>
  </el-breadcrumb>
</template>
