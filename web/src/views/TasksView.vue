<script setup lang="ts">
import { ref } from 'vue'
import { useTasks } from '@/composables/useTasks'
import StatusBadge from '@/components/StatusBadge.vue'
import ApprovalPanel from '@/components/ApprovalPanel.vue'

const { tasks, pending, connected, error, create, answer } = useTasks()
const issueUrl = ref('')
const submitting = ref(false)
const submitError = ref<string | null>(null)

async function submit() {
  if (!issueUrl.value) return
  submitting.value = true
  submitError.value = null
  try {
    await create(issueUrl.value)
    issueUrl.value = ''
  } catch (e) {
    submitError.value = e instanceof Error ? e.message : String(e)
  } finally {
    submitting.value = false
  }
}

function time(iso: string) {
  return new Date(iso).toLocaleTimeString()
}
</script>

<template>
  <section class="tasks">
    <header class="row">
      <h1>任务</h1>
      <span class="conn" :data-on="connected">{{ connected ? '实时' : '离线' }}</span>
    </header>

    <form class="row" @submit.prevent="submit">
      <input
        v-model.trim="issueUrl"
        type="url"
        required
        placeholder="https://github.com/owner/repo/issues/123"
        aria-label="Issue URL"
      />
      <button type="submit" :disabled="submitting">创建任务</button>
    </form>
    <p v-if="submitError" class="error">{{ submitError }}</p>
    <p v-if="error" class="error">{{ error }}</p>

    <p v-if="tasks.length === 0" class="muted">还没有任务。</p>
    <table v-else>
      <thead>
        <tr>
          <th>标题</th>
          <th>状态</th>
          <th>更新</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="t in tasks" :key="t.id">
          <tr>
            <td>
              <a :href="t.issue_url" target="_blank" rel="noreferrer">{{ t.title }}</a>
            </td>
            <td><StatusBadge :status="t.status" /></td>
            <td class="muted">{{ time(t.updated_at) }}</td>
          </tr>
          <tr v-if="pending[t.id]?.length" class="gate">
            <td colspan="3">
              <ApprovalPanel
                v-for="a in pending[t.id]"
                :key="a.id"
                :approval="a"
                :answer="answer"
              />
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.tasks {
  max-width: 56rem;
}
.row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
}
h1 {
  font-size: 1.4rem;
  margin: 0;
}
.conn {
  font-size: 0.8rem;
  color: var(--color-text);
  opacity: 0.6;
}
.conn[data-on='true'] {
  color: hsla(160, 100%, 37%, 1);
  opacity: 1;
}
input {
  flex: 1;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-background);
  color: var(--color-text);
}
button {
  padding: 0.45rem 0.9rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-background-soft);
  color: var(--color-heading);
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  text-align: left;
  padding: 0.5rem 0.4rem;
  border-bottom: 1px solid var(--color-border);
}
th {
  font-weight: 500;
  opacity: 0.7;
}
.muted {
  opacity: 0.6;
}
.gate > td {
  border-bottom: 1px solid var(--color-border);
  padding: 0 0.4rem;
}
.error {
  color: #f85149;
}
</style>
