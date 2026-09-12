// Task list kept live by the server's event stream.
//
// Load once over HTTP, then apply events as they arrive. EventSource reconnects
// by itself and sends `Last-Event-ID`, so the server replays whatever we missed;
// `apply` is idempotent so a replayed event is harmless.
import { onMounted, onUnmounted, ref } from 'vue'
import { api, type Task, type TaskEvent, type TaskStatus } from '@/api/client'

export function useTasks() {
  const tasks = ref<Task[]>([])
  const connected = ref(false)
  const error = ref<string | null>(null)
  let source: EventSource | null = null

  function apply(ev: TaskEvent) {
    const i = tasks.value.findIndex((t) => t.id === ev.task_id)
    if (ev.type === 'task.created' && i === -1) {
      tasks.value.unshift({
        id: ev.task_id,
        title: String(ev.payload.title ?? ev.task_id),
        issue_url: String(ev.payload.issue_url ?? ''),
        status: 'queued',
        created_at: ev.ts,
        updated_at: ev.ts,
      })
    } else if (ev.type === 'task.status_changed' && i !== -1) {
      const current = tasks.value[i]!
      tasks.value[i] = { ...current, status: ev.payload.to as TaskStatus, updated_at: ev.ts }
    }
  }

  async function load() {
    try {
      tasks.value = await api.listTasks()
      error.value = null
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
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

  return { tasks, connected, error, create, reload: load }
}
