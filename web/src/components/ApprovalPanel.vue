<script setup lang="ts">
// One approval envelope. A plan is rendered field by field -- that is the whole
// point of having made it structured: the reviewer reads a diagnosis and a list
// of cited spans, not a wall of JSON. Anything else falls back to the raw
// payload, which is still what the hash covers.
//
// The hash is shown, not hidden: it is the identity of what is being approved,
// and it travels back with the answer so the server can refuse a swapped
// payload. Buttons disable while a request is in flight; the row itself only
// disappears once the event stream reports the decision.
import { computed, ref } from 'vue'
import type { Approval } from '@/api/client'
import DiffView from '@/components/DiffView.vue'

type Location = { file: string; line_start: number; line_end: number; why: string }
type Plan = {
  diagnosis: string
  locations: Location[]
  approach: string
  verification?: string
  confidence?: string
}

const props = defineProps<{
  approval: Approval
  answer: (approval: Approval, decision: 'approve' | 'reject') => Promise<void>
}>()

const busy = ref<'approve' | 'reject' | null>(null)
const error = ref<string | null>(null)

const KIND: Record<string, string> = {
  plan: '修复计划',
  patch: '代码改动',
  shell: 'Shell 命令',
  push: '推送分支',
}

const CONFIDENCE: Record<string, string> = { high: '高', medium: '中', low: '低' }

const plan = computed<Plan | null>(() => {
  const candidate = props.approval.payload?.plan as Partial<Plan> | undefined
  // Older envelopes are still in the database, and a payload is only ever as
  // structured as whatever produced it; render fields we recognise, not a crash.
  if (props.approval.kind !== 'plan' || typeof candidate?.diagnosis !== 'string') return null
  return {
    diagnosis: candidate.diagnosis,
    locations: Array.isArray(candidate.locations) ? candidate.locations : [],
    approach: typeof candidate.approach === 'string' ? candidate.approach : '',
    verification: candidate.verification,
    confidence: candidate.confidence,
  }
})

const patch = computed(() => {
  const candidate = props.approval.payload?.patch
  return props.approval.kind === 'patch' && typeof candidate === 'string' ? candidate : null
})

const changed = computed(() => {
  const files = props.approval.payload?.changed
  return Array.isArray(files) ? (files as string[]) : []
})

const run = computed(() => {
  const { steps, cost_usd: cost, forced } = props.approval.payload as Record<string, unknown>
  return typeof steps === 'number' ? { steps, cost: Number(cost ?? 0), forced: !!forced } : null
})

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

    <template v-if="plan">
      <p class="diagnosis">{{ plan.diagnosis }}</p>

      <ol v-if="plan.locations.length" class="locations">
        <li v-for="(loc, i) in plan.locations" :key="i">
          <code
            >{{ loc.file }}:{{ loc.line_start
            }}<span v-if="loc.line_end !== loc.line_start">-{{ loc.line_end }}</span></code
          >
          <span class="why">{{ loc.why }}</span>
        </li>
      </ol>
      <p v-else class="muted">没有给出具体位置。</p>

      <dl class="fields">
        <template v-if="plan.approach">
          <dt>改法</dt>
          <dd>{{ plan.approach }}</dd>
        </template>
        <template v-if="plan.verification">
          <dt>验证</dt>
          <dd>{{ plan.verification }}</dd>
        </template>
      </dl>

      <p v-if="run" class="run muted">
        <span>{{ run.steps }} 步</span>
        <span>${{ run.cost.toFixed(6) }}</span>
        <span v-if="plan.confidence"
          >信心 {{ CONFIDENCE[plan.confidence] ?? plan.confidence }}</span
        >
        <span v-if="run.forced" class="forced">步数/预算耗尽后被迫交卷</span>
      </p>

      <details>
        <summary class="muted">原始信封</summary>
        <pre>{{ body }}</pre>
      </details>
    </template>

    <template v-else-if="patch !== null">
      <p class="diagnosis">{{ approval.subject }}</p>

      <ul v-if="changed.length" class="files">
        <li v-for="file in changed" :key="file">
          <code>{{ file }}</code>
        </li>
      </ul>

      <DiffView :patch="patch" />

      <p v-if="run" class="run muted">
        <span>{{ run.steps }} 步</span>
        <span>${{ run.cost.toFixed(6) }}</span>
        <span v-if="run.forced" class="forced">步数/预算耗尽后被迫收尾</span>
      </p>
    </template>

    <template v-else>
      <p class="subject">{{ approval.subject }}</p>
      <pre>{{ body }}</pre>
    </template>

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
.diagnosis {
  margin: 0 0 0.7rem;
  line-height: 1.5;
}
.locations {
  margin: 0 0 0.7rem;
  padding-left: 1.2rem;
}
.files {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin: 0 0 0.6rem;
  padding: 0;
  list-style: none;
  font-size: 0.8rem;
}
.locations li {
  margin-bottom: 0.35rem;
}
.locations code {
  display: inline-block;
  margin-right: 0.5rem;
  color: var(--color-heading);
}
.why {
  opacity: 0.75;
}
.fields {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.25rem 0.75rem;
  margin: 0 0 0.6rem;
}
.fields dt {
  opacity: 0.6;
  white-space: nowrap;
}
.fields dd {
  margin: 0;
  line-height: 1.5;
}
.run {
  display: flex;
  gap: 1rem;
  margin: 0 0 0.6rem;
}
.forced {
  color: #d29922;
  opacity: 1;
}
details {
  margin-bottom: 0.6rem;
}
summary {
  cursor: pointer;
  font-size: 0.8rem;
}
pre {
  margin: 0.4rem 0 0.6rem;
  white-space: pre-wrap;
  word-break: break-word;
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
