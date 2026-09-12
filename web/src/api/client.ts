// Thin typed wrapper over fetch. Types come from the backend's OpenAPI document
// (`npm run gen:api`), so a schema change fails `vue-tsc` here instead of at runtime.
import type { components } from './schema'

export type Task = components['schemas']['Task']
export type TaskCreate = components['schemas']['TaskCreate']
export type TaskStatus = components['schemas']['TaskStatus']
// "Event" would shadow the DOM type.
export type TaskEvent = components['schemas']['Event']

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<T>
}

export const api = {
  listTasks: (): Promise<Task[]> => fetch('/api/tasks').then(json<Task[]>),
  createTask: (body: TaskCreate): Promise<Task> =>
    fetch('/api/tasks', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    }).then(json<Task>),
  taskEvents: (id: string): Promise<TaskEvent[]> =>
    fetch(`/api/tasks/${id}/events`).then(json<TaskEvent[]>),
}
