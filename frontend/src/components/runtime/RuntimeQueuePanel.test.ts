import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { runtimeJobSummary, runtimeKindLabel, runtimeStatusLabel } from './RuntimeQueuePanel'
import { RuntimeQueuePanel } from './RuntimeQueuePanel'


const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  remove: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))

vi.mock('../../shared/api', () => ({
  api: { get: mocks.get, post: mocks.post, delete: mocks.remove },
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: mocks.success, error: mocks.error },
  ElMessageBox: { confirm: vi.fn() },
}))


describe('RuntimeQueuePanel formatters', () => {
  afterEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it('uses user-facing labels for queue kinds and statuses', () => {
    expect(runtimeKindLabel('message_batch')).toBe('批量自动私信')
    expect(runtimeStatusLabel('interrupted')).toBe('被中断')
    expect(runtimeKindLabel('custom_job')).toBe('custom_job')
  })

  it('summarizes resource, attempts and the latest error', () => {
    expect(runtimeJobSummary({ resource: 'browser', attempt: 1, max_attempts: 2, error: '登录失效' }))
      .toBe('资源：浏览器 · 尝试：1/2 · 登录失效')
  })

  it('loads queue rows and sends cancellation from the rendered panel', async () => {
    vi.useFakeTimers()
    mocks.get.mockResolvedValue({
      data: {
        total: 1,
        active: { browser: 0, ai: 0, default: 0 },
        items: [{ id: 'runtime-1', kind: 'crawl_task', entity_id: 'crawl-1', status: 'queued', resource: 'browser', attempt: 0, max_attempts: 1 }],
      },
    })
    mocks.post.mockResolvedValue({ data: { status: 'cancelled' } })
    const wrapper = mount(RuntimeQueuePanel)
    await flushPromises()

    expect(wrapper.text()).toContain('采集任务')
    expect(wrapper.text()).toContain('排队中')
    await wrapper.get('.runtime-job-actions button').trigger('click')
    await flushPromises()

    expect(mocks.post).toHaveBeenCalledWith('/runtime/jobs/runtime-1/cancel')
    expect(mocks.success).toHaveBeenCalledWith('已请求取消任务')
    wrapper.unmount()
  })
})
