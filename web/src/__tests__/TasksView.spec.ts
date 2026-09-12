import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import TasksView from '@/views/TasksView.vue'
import type { Task, TaskEvent } from '@/api/client'

const TASK: Task = {
  id: 'abc',
  title: 'octo/repo#7',
  issue_url: 'https://github.com/octo/repo/issues/7',
  status: 'queued',
  created_at: '2026-09-12T10:00:00Z',
  updated_at: '2026-09-12T10:00:00Z',
}

class FakeEventSource {
  static last: FakeEventSource | null = null
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  private listeners = new Map<string, (e: MessageEvent) => void>()
  constructor(public url: string) {
    FakeEventSource.last = this
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(type, fn)
  }
  emit(ev: TaskEvent) {
    this.listeners.get(ev.type)?.(new MessageEvent(ev.type, { data: JSON.stringify(ev) }))
  }
  close() {}
}

function ok(body: unknown, status = 200) {
  return { ok: true, status, statusText: 'OK', json: async () => body } as Response
}

describe('TasksView', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('EventSource', FakeEventSource)
    fetchMock.mockReset()
  })
  afterEach(() => vi.unstubAllGlobals())

  it('loads tasks, then updates a row when a status event arrives', async () => {
    fetchMock.mockResolvedValueOnce(ok([TASK]))
    const wrapper = mount(TasksView)
    await flushPromises()

    expect(wrapper.text()).toContain('octo/repo#7')
    expect(wrapper.text()).toContain('排队中')

    FakeEventSource.last!.onopen?.()
    FakeEventSource.last!.emit({
      id: 2,
      task_id: 'abc',
      type: 'task.status_changed',
      payload: { from: 'queued', to: 'awaiting_approval' },
      ts: '2026-09-12T10:00:01Z',
    })
    await flushPromises()

    expect(wrapper.text()).toContain('待审批')
    expect(wrapper.text()).toContain('实时')
  })

  it('creates a task from the form and shows it at the top', async () => {
    fetchMock.mockResolvedValueOnce(ok([]))
    const wrapper = mount(TasksView)
    await flushPromises()
    expect(wrapper.text()).toContain('还没有任务')

    fetchMock.mockResolvedValueOnce(ok(TASK, 201))
    await wrapper.find('input').setValue(TASK.issue_url)
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const [url, init] = fetchMock.mock.calls[1]!
    expect(url).toBe('/api/tasks')
    expect(JSON.parse(init.body)).toEqual({ issue_url: TASK.issue_url })
    expect(wrapper.text()).toContain('octo/repo#7')
  })
})
