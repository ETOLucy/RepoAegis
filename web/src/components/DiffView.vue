<script setup lang="ts">
// A unified diff, coloured. Parsing here is deliberately shallow: git already
// decided what changed, and re-deriving that in the browser would be a second
// source of truth that can disagree with the patch the approval hash covers.
// Every line is shown exactly as the patch holds it.
import { computed } from 'vue'

const props = defineProps<{ patch: string; maxLines?: number }>()

type Kind = 'file' | 'hunk' | 'add' | 'remove' | 'context' | 'meta'
type Row = { kind: Kind; text: string }

const LIMIT = 600

function classify(line: string): Kind {
  if (line.startsWith('diff --git')) return 'file'
  if (line.startsWith('@@')) return 'hunk'
  if (line.startsWith('+++') || line.startsWith('---')) return 'meta'
  if (line.startsWith('index ') || line.startsWith('new file') || line.startsWith('deleted file'))
    return 'meta'
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'remove'
  return 'context'
}

const rows = computed<Row[]>(() => {
  const limit = props.maxLines ?? LIMIT
  return props.patch
    .split('\n')
    .slice(0, limit)
    .map((text) => ({ kind: classify(text), text }))
})

const truncated = computed(() => {
  const total = props.patch.split('\n').length
  const limit = props.maxLines ?? LIMIT
  return total > limit ? total - limit : 0
})

const counts = computed(() => {
  let added = 0
  let removed = 0
  for (const line of props.patch.split('\n')) {
    const kind = classify(line)
    if (kind === 'add') added += 1
    if (kind === 'remove') removed += 1
  }
  return { added, removed }
})
</script>

<template>
  <div class="diff">
    <p class="tally">
      <span class="plus">+{{ counts.added }}</span>
      <span class="minus">−{{ counts.removed }}</span>
    </p>
    <pre><code><span
        v-for="(row, i) in rows"
        :key="i"
        class="line"
        :data-kind="row.kind"
      >{{ row.text || ' ' }}
</span></code></pre>
    <p v-if="truncated" class="muted">… 还有 {{ truncated }} 行未显示</p>
  </div>
</template>

<style scoped>
.diff {
  margin-bottom: 0.7rem;
}
.tally {
  display: flex;
  gap: 0.6rem;
  margin: 0 0 0.35rem;
  font-size: 0.8rem;
}
.plus {
  color: hsla(160, 100%, 37%, 1);
}
.minus {
  color: #f85149;
}
pre {
  margin: 0;
  padding: 0;
  max-height: 28rem;
  overflow: auto;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-background);
  font-size: 0.78rem;
  line-height: 1.5;
}
.line {
  display: block;
  padding: 0 0.5rem;
  white-space: pre;
}
.line[data-kind='add'] {
  background: rgba(46, 160, 67, 0.15);
  color: hsla(160, 100%, 30%, 1);
}
.line[data-kind='remove'] {
  background: rgba(248, 81, 73, 0.12);
  color: #d1383a;
}
.line[data-kind='hunk'] {
  color: #8250df;
  background: rgba(130, 80, 223, 0.08);
}
.line[data-kind='file'],
.line[data-kind='meta'] {
  opacity: 0.55;
}
.muted {
  opacity: 0.6;
  font-size: 0.8rem;
  margin: 0.35rem 0 0;
}
</style>
