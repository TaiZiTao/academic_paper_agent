<script setup lang="ts">
import { computed } from "vue";
import { ArrowRight, Check, Loading } from "@element-plus/icons-vue";
import type { PaperSection } from "@/types/paper";

interface SectionNode extends PaperSection {
  children: SectionNode[];
}

const props = defineProps<{
  sections: PaperSection[];
  selectedSection: string | null;
  completedSections: string[];
  runningSection: string | null;
}>();

const emit = defineEmits<{
  select: [section: PaperSection];
  openPage: [page: number];
}>();

const treeData = computed<SectionNode[]>(() => {
  const roots: SectionNode[] = [];
  const stack: SectionNode[] = [];
  for (const section of props.sections) {
    const node: SectionNode = { ...section, children: [] };
    while (stack.length && stack[stack.length - 1].level >= node.level) stack.pop();
    if (stack.length) stack[stack.length - 1].children.push(node);
    else roots.push(node);
    stack.push(node);
  }
  return roots;
});

function isCompleted(title: string) {
  return props.completedSections.includes(title);
}

function handleNodeClick(data: SectionNode) {
  emit("select", data);
}
</script>

<template>
  <aside class="section-tree-panel">
    <div class="tree-heading">
      <span>CHAPTER MAP</span>
      <strong>{{ sections.length }} 节</strong>
    </div>
    <el-tree
      class="section-tree"
      :data="treeData"
      node-key="id"
      default-expand-all
      highlight-current
      :expand-on-click-node="false"
      :current-node-key="sections.find((item) => item.title === selectedSection)?.id"
      @node-click="handleNodeClick"
    >
      <template #default="{ data }: { data: SectionNode }">
        <div class="tree-node" :class="{ selected: data.title === selectedSection }">
          <el-icon v-if="data.children.length" class="branch-icon"><ArrowRight /></el-icon>
          <span v-else class="branch-dot" />
          <div class="node-copy">
            <strong>{{ data.title }}</strong>
            <button type="button" @click.stop="emit('openPage', data.page_start)">
              p.{{ data.page_start }}<template v-if="data.page_end !== data.page_start">–{{ data.page_end }}</template>
            </button>
            <small v-if="data.children.length" class="child-hint">整章翻译 · 含 {{ data.children.length }} 个子章节</small>
          </div>
          <el-icon v-if="runningSection === data.title" class="status-icon spinning"><Loading /></el-icon>
          <el-icon v-else-if="isCompleted(data.title)" class="status-icon complete"><Check /></el-icon>
        </div>
      </template>
    </el-tree>
  </aside>
</template>

<style scoped>
.section-tree-panel { min-width: 0; border: 1px solid #d9d2c5; background: rgba(255, 253, 247, .78); }
.tree-heading { display: flex; align-items: baseline; justify-content: space-between; padding: 15px 16px 12px; border-bottom: 1px solid #ded8cc; }
.tree-heading span { color: #b45f42; font-size: 9px; letter-spacing: .18em; font-weight: 700; }
.tree-heading strong { color: #7c888b; font-size: 11px; font-weight: 500; }
.section-tree { padding: 9px 8px 13px; background: transparent; --el-tree-node-hover-bg-color: #f5e9df; }
.section-tree :deep(.el-tree-node__content) { height: auto; min-height: 42px; margin: 2px 0; padding: 0 5px !important; border-radius: 4px; }
.section-tree :deep(.el-tree-node.is-current > .el-tree-node__content) { background: #f3e3d8; }
.tree-node { width: 100%; min-width: 0; display: grid; grid-template-columns: 14px minmax(0, 1fr) 16px; align-items: center; gap: 7px; padding: 6px 2px; }
.branch-icon { color: #a29b90; }
.branch-dot { width: 4px; height: 4px; margin-left: 5px; border-radius: 50%; background: #b9b1a5; }
.node-copy { min-width: 0; display: grid; gap: 3px; }
.node-copy strong { overflow: hidden; color: #28424a; font: 500 14px/1.35 Georgia, "Noto Serif SC", serif; text-overflow: ellipsis; white-space: nowrap; }
.node-copy button { width: max-content; padding: 0; border: 0; background: none; color: #8a9698; font-size: 9px; cursor: pointer; }
.node-copy button:hover { color: #b45f42; }
.child-hint { color: #a8a092; font-size: 9px; line-height: 1.2; }
.selected .node-copy strong { color: #a84f33; }
.status-icon.complete { color: #64846d; }
.status-icon.spinning { color: #b45f42; animation: spin .9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
