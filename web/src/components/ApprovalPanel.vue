<script setup lang="ts">
// One approval envelope, rendered exactly as the server stored it.
//
// The hash is shown, not hidden: it is the identity of what is being approved,
// and it travels back with the answer so the server can refuse a swapped
// payload. Buttons disable while a request is in flight; the row itself only
// disappears once the event stream reports the decision.
import { computed, ref } from 'vue'
import type { Approval } from '@/api/client'

const props = defineProps<{
  approval: Approval
  answer: (approval: Approval, decision: 'approve' | 'reject') => Promise<void>
}>()

const busy = ref<'approve' | 'reject' | null>(null)
const error = ref<string | null>(null)

const KIND: Record<string, string> = {
  plan: '修复计划',
  shell: 'Shell 命令',
  push: '推送分支',
}

const body = computed(() => JSON.stringify(props.approval.payload, null, 2))
const expires = computed(() => new Date(props.approval.expires_at).toLocaleString())

async function send(decision: 'approve' | 'reject') {
  busy.value = decision
  error.value = null
  try {
    await props.answer(props.approval, decision)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div class="panel">
    <div class="head">
      <strong>{{ KIND[approval.kind] ?? approval.kind }}</strong>
      <span class="muted">{{ approval.reason }}</span>
    </div>

    <p class="subject">{{ approval.subject }}</p>
    <pre>{{ body }}</pre>

    <dl class="meta">
      <dt>内容哈希</dt>
      <dd>
        <code>{{ approval.payload_hash.slice(0, 16) }}…</code>
      </dd>
      <dt>策略</dt>
      <dd>{{ approval.policy }}</dd>
      <dt>过期</dt>
      <dd>{{ expires }}</dd>
    </dl>

    <div class="actions">
      <button class="approve" :disabled="busy !== null" @click="send('approve')">
        {{ busy === 'approve' ? '提交中…' : '批准' }}
      </button>
      <button class="reject" :disabled="busy !== null" @click="send('reject')">
        {{ busy === 'reject' ? '提交中…' : '驳回' }}
      </button>
      <span v-if="busy" class="muted">等待事件流确认…</span>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
  </div>
</template>

<style scoped>
.panel {
  border: 1px solid #d29922;
  border-radius: 6px;
  padding: 0.75rem 0.9rem;
  margin: 0.25rem 0 0.75rem;
  background: var(--color-background-soft);
}
.head {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  margin-bottom: 0.4rem;
}
.subject {
  margin: 0 0 0.5rem;
}
pre {
  margin: 0 0 0.6rem;
  padding: 0.6rem;
  overflow-x: auto;
  font-size: 0.8rem;
  line-height: 1.45;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-background);
}
.meta {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.15rem 0.75rem;
  margin: 0 0 0.7rem;
  font-size: 0.8rem;
}
.meta dt {
  opacity: 0.6;
}
.meta dd {
  margin: 0;
}
.actions {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
button {
  padding: 0.35rem 1rem;
  border-radius: 4px;
  border: 1px solid var(--color-border);
  background: var(--color-background);
  color: var(--color-heading);
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
.approve {
  border-color: hsla(160, 100%, 37%, 1);
  color: hsla(160, 100%, 37%, 1);
}
.reject {
  border-color: #f85149;
  color: #f85149;
}
.muted {
  opacity: 0.6;
  font-size: 0.8rem;
}
.error {
  color: #f85149;
  margin: 0.5rem 0 0;
}
</style>
