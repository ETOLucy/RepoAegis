// The console's half of the approval gate: show the envelope, answer it, and
// let the event stream -- not the click -- be what changes the screen.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import TasksView from '@/views/TasksView.vue'
import type { Approval, Task, TaskEvent } from '@/api/client'

const TASK: Task = {
  id: 'abc',
  title: 'octo/repo#7',
  issue_url: 'https://github.com/octo/repo/issues/7',
  status: 'awaiting_approval',
  created_at: '2026-09-13T10:00:00Z',
  updated_at: '2026-09-13T10:00:00Z',
  steps: 0,
  cost_usd: 0,
}

const APPROVAL: Approval = {
  id: 'gate-1',
  task_id: 'abc',
  kind: 'plan',
  subject: '给重定向处理打补丁',
  payload: {
    plan: {
      diagnosis: '给重定向处理打补丁',
      locations: [
        { file: 'src/sessions.py', line_start: 691, line_end: 700, why: '重定向在这里丢掉片段' },
      ],
      approach: '把片段带过去',
      verification: 'pytest tests/test_redirects.py',
      confidence: 'high',
    },
    steps: 5,
    cost_usd: 0.000714,
    forced: false,
  },
  payload_hash: 'a'.repeat(64),
  status: 'pending',
  policy: 'default',
  reason: 'plans are reviewed before execution',
  decided_by: null,
  created_at: '2026-09-13T10:00:01Z',
  expires_at: '2026-09-13T11:00:01Z',
  decided_at: null,
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

describe('approval flow', () => {
  const fetchMock = vi.fn()
  let openGates: Approval[] = []

  beforeEach(() => {
    openGates = [APPROVAL]
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.stubGlobal(
      'fetch',
      fetchMock.mockImplementation((url: string) => {
        if (url === '/api/tasks') return Promise.resolve(ok([TASK]))
        if (url === '/api/tasks/abc/approvals') return Promise.resolve(ok(openGates))
        if (url === '/api/approvals/gate-1/decision') {
          openGates = []
          return Promise.resolve(ok({ ...APPROVAL, status: 'approved' }))
        }
        throw new Error(`unexpected fetch ${url}`)
      }),
    )
  })
  afterEach(() => vi.unstubAllGlobals())

  it('shows the envelope for a waiting task', async () => {
    const wrapper = mount(TasksView)
    await flushPromises()

    expect(wrapper.text()).toContain('待审批')
    expect(wrapper.text()).toContain('修复计划')
    expect(wrapper.text()).toContain('给重定向处理打补丁')
    // The structured plan is rendered as fields, not as a blob of JSON.
    expect(wrapper.text()).toContain('src/sessions.py:691-700')
    expect(wrapper.text()).toContain('重定向在这里丢掉片段')
    expect(wrapper.text()).toContain('5 步')
    expect(wrapper.text()).toContain(APPROVAL.payload_hash.slice(0, 16))
  })

  it('sends the hash it displayed, and waits for the stream to update the row', async () => {
    const wrapper = mount(TasksView)
    await flushPromises()
    FakeEventSource.last!.onopen?.()

    await wrapper.find('button.approve').trigger('click')
    await flushPromises()

    const call = fetchMock.mock.calls.find((c) => c[0] === '/api/approvals/gate-1/decision')!
    expect(JSON.parse(call[1].body)).toEqual({
      decision: 'approve',
      payload_hash: APPROVAL.payload_hash,
    })
    expect(call[1].headers['x-actor']).toBe('user:console')

    // The click alone must not have moved anything on screen.
    expect(wrapper.text()).toContain('待审批')

    FakeEventSource.last!.emit({
      id: 5,
      task_id: 'abc',
      type: 'approval.decided',
      payload: { approval_id: 'gate-1', status: 'approved' },
      ts: '2026-09-13T10:00:05Z',
    })
    FakeEventSource.last!.emit({
      id: 6,
      task_id: 'abc',
      type: 'task.status_changed',
      payload: { from: 'awaiting_approval', to: 'solving' },
      ts: '2026-09-13T10:00:05Z',
    })
    await flushPromises()

    expect(wrapper.text()).toContain('求解中')
    expect(wrapper.find('button.approve').exists()).toBe(false)
  })

  it('surfaces a rejected answer instead of pretending it worked', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url === '/api/tasks') return Promise.resolve(ok([TASK]))
      if (url === '/api/tasks/abc/approvals') return Promise.resolve(ok([APPROVAL]))
      return Promise.resolve({ ok: false, status: 409, statusText: 'Conflict' } as Response)
    })
    const wrapper = mount(TasksView)
    await flushPromises()

    await wrapper.find('button.approve').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('409')
    expect(wrapper.text()).toContain('待审批')
  })

  it('renders an envelope whose plan is missing fields instead of crashing', async () => {
    openGates = [{ ...APPROVAL, payload: { plan: { summary: 'from an older run' } } }]
    const wrapper = mount(TasksView)
    await flushPromises()

    expect(wrapper.text()).toContain('待审批')
    expect(wrapper.find('button.approve').exists()).toBe(true)
  })
})
