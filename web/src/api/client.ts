// Thin typed wrapper over fetch. Types come from the backend's OpenAPI document
// (`npm run gen:api`), so a schema change fails `vue-tsc` here instead of at runtime.
import type { components } from './schema'

export type Task = components['schemas']['Task']
export type TaskCreate = components['schemas']['TaskCreate']
export type TaskStatus = components['schemas']['TaskStatus']
// "Event" would shadow the DOM type.
export type TaskEvent = components['schemas']['Event']
export type Approval = components['schemas']['Approval']
export type DecisionRequest = components['schemas']['DecisionRequest']

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
  taskApprovals: (id: string): Promise<Approval[]> =>
    fetch(`/api/tasks/${id}/approvals`).then(json<Approval[]>),
  // The hash the reviewer actually saw travels with the answer: the server
  // refuses it if the envelope now covers something else.
  decide: (approvalId: string, body: DecisionRequest, actor: string): Promise<Approval> =>
    fetch(`/api/approvals/${approvalId}/decision`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-actor': actor },
      body: JSON.stringify(body),
    }).then(json<Approval>),
}
