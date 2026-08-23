<script setup lang="ts">
/**
 * 知识库 Dialog — 新建 / 编辑复用
 *
 * Props:
 *   visible — 是否显示
 *   title   — Dialog 标题
 *   loading — 提交中
 *
 * Emits:
 *   update:visible — v-model 双向绑定
 *   submit          — 表单提交
 */

defineProps<{
  visible: boolean;
  title: string;
  loading?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:visible", v: boolean): void;
  (e: "submit"): void;
}>();

const formName = defineModel<string>("formName", { default: "" });
const formDesc = defineModel<string>("formDesc", { default: "" });

function onClose() {
  emit("update:visible", false);
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    :title="title"
    width="500px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:visible', $event)"
  >
    <el-form label-width="80px" @submit.prevent="emit('submit')">
      <el-form-item label="名称" required>
        <el-input v-model="formName" placeholder="请输入知识库名称" maxlength="128" />
      </el-form-item>
      <el-form-item label="描述">
        <el-input
          v-model="formDesc"
          type="textarea"
          :rows="3"
          placeholder="请输入知识库描述（可选）"
          maxlength="2000"
          show-word-limit
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="onClose">取消</el-button>
      <el-button type="primary" :loading="loading" @click="emit('submit')">
        确定
      </el-button>
    </template>
  </el-dialog>
</template>
