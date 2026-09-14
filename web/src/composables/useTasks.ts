// Task list kept live by the server's event stream.
//
// Load once over HTTP, then apply events as they arrive. EventSource reconnects
// by itself and sends `Last-Event-ID`, so the server replays whatever we missed;
// `apply` is idempotent so a replayed event is harmless.
import { onMounted, onUnmounted, ref } from 'vue'
import { api, type Approval, type Task, type TaskEvent, type TaskStatus } from '@/api/client'

// No accounts yet; the audit trail still wants to know who answered a gate.
const ACTOR = 'user:console'

export function useTasks() {
  const tasks = ref<Task[]>([])
  // task id -> its still-open approval envelopes.
  const pending = ref<Record<string, Approval[]>>({})
  const connected = ref(false)
  const error = ref<string | null>(null)
  let source: EventSource | null = null

  function apply(ev: TaskEvent) {
    if (ev.type.startsWith('approval.')) {
      // The event carries a summary; the envelope itself is fetched so the
      // panel always shows exactly what the server would check the hash against.
      void loadApprovals(ev.task_id)
      return
    }
    const i = tasks.value.findIndex((t) => t.id === ev.task_id)
    if (ev.type === 'task.created' && i === -1) {
      tasks.value.unshift({
        id: ev.task_id,
        title: String(ev.payload.title ?? ev.task_id),
        issue_url: String(ev.payload.issue_url ?? ''),
        status: 'queued',
        created_at: ev.ts,
        updated_at: ev.ts,
        steps: 0,
        cost_usd: 0,
      })
    } else if (ev.type === 'task.status_changed' && i !== -1) {
      const current = tasks.value[i]!
      tasks.value[i] = { ...current, status: ev.payload.to as TaskStatus, updated_at: ev.ts }
    } else if (ev.type === 'plan.finished' && i !== -1) {
      // The run's cost lands before the status moves; show it as soon as it is known.
      const current = tasks.value[i]!
      tasks.value[i] = {
        ...current,
        steps: Number(ev.payload.steps ?? current.steps),
        cost_usd: Number(ev.payload.cost_usd ?? current.cost_usd),
      }
    }
  }

  async function loadApprovals(taskId: string) {
    const all = await api.taskApprovals(taskId)
    pending.value = { ...pending.value, [taskId]: all.filter((a) => a.status === 'pending') }
  }

  async function load() {
    try {
      tasks.value = await api.listTasks()
      error.value = null
      await Promise.all(
        tasks.value.filter((t) => t.status === 'awaiting_approval').map((t) => loadApprovals(t.id)),
      )
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  }

  // Answering never writes the outcome locally: the server decides, and the
  // event stream reports it. Without a stream we re-read instead of guessing.
  async function answer(approval: Approval, decision: 'approve' | 'reject') {
    await api.decide(approval.id, { decision, payload_hash: approval.payload_hash }, ACTOR)
    if (!connected.value) {
      await loadApprovals(approval.task_id)
      await load()
    }
  }

  function connect() {
    if (typeof EventSource === 'undefined') return
    source = new EventSource('/api/events')
    source.onopen = () => (connected.value = true)
    source.onerror = () => (connected.value = false)
    const onEvent = (e: Event) => apply(JSON.parse((e as MessageEvent<string>).data) as TaskEvent)
    source.addEventListener('task.created', onEvent)
    source.addEventListener('task.status_changed', onEvent)
    source.addEventListener('plan.finished', onEvent)
    source.addEventListener('approval.requested', onEvent)
    source.addEventListener('approval.decided', onEvent)
  }

  async function create(issue_url: string): Promise<Task> {
    const task = await api.createTask({ issue_url })
    if (!tasks.value.some((t) => t.id === task.id)) tasks.value.unshift(task)
    return task
  }

  onMounted(async () => {
    await load()
    connect()
  })
  onUnmounted(() => source?.close())

  return { tasks, pending, connected, error, create, answer, reload: load }
}
