<script setup lang="ts">
import type { TaskStatus } from '@/api/client'

defineProps<{ status: TaskStatus }>()

const LABEL: Record<TaskStatus, string> = {
  queued: '排队中',
  planning: '规划中',
  awaiting_approval: '待审批',
  solving: '求解中',
  awaiting_patch_approval: '待审补丁',
  verifying: '验证中',
  delivering: '交付中',
  done: '完成',
  failed: '失败',
  rejected: '已驳回',
}
</script>

<template>
  <span class="badge" :data-status="status">{{ LABEL[status] }}</span>
</template>

<style scoped>
.badge {
  display: inline-block;
  padding: 0.1rem 0.55rem;
  border-radius: 999px;
  font-size: 0.8rem;
  white-space: nowrap;
  border: 1px solid var(--color-border);
  color: var(--color-text);
}
.badge[data-status='awaiting_approval'],
.badge[data-status='awaiting_patch_approval'] {
  border-color: #d29922;
  color: #d29922;
}
.badge[data-status='done'] {
  border-color: hsla(160, 100%, 37%, 1);
  color: hsla(160, 100%, 37%, 1);
}
.badge[data-status='failed'],
.badge[data-status='rejected'] {
  border-color: #f85149;
  color: #f85149;
}
</style>
