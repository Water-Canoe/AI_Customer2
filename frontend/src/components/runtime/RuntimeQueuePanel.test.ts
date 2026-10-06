import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { runtimeJobSummary, runtimeKindLabel, runtimeResourceLabel, runtimeStatusLabel } from './RuntimeQueuePanel'
import { RuntimeQueuePanel } from './RuntimeQueuePanel'


const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  remove: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push: mocks.push }) }))

vi.mock('../../shared/api', () => ({
  api: { get: mocks.get, post: mocks.post, delete: mocks.remove },
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: mocks.success, error: mocks.error },
  ElMessageBox: { confirm: vi.fn() },
  ElPagination: { render: () => null },
}))


describe('RuntimeQueuePanel formatters', () => {
  afterEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it('uses user-facing labels for queue kinds and statuses', () => {
    expect(runtimeKindLabel('message_batch')).toBe('批量自动私信')
    expect(runtimeStatusLabel('interrupted')).toBe('被中断')
    expect(runtimeKindLabel('automation_run')).toBe('自动化计划')
    expect(runtimeKindLabel('content_publish')).toBe('内容发布')
    expect(runtimeResourceLabel('video')).toBe('视频')
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

  it('定位历史任务，并仅在服务端允许时提供重试', async () => {
    const job = { id: 'old-job', kind: 'content_publish', status: 'failed', display_name: '发布给品牌号', detail_url: '/content-publish?task=publish-1', retry_allowed: false }
    mocks.get.mockImplementation(async (url: string) => ({ data: url === '/runtime/jobs/old-job' ? job : { items: [], total: 0, active: {} } }))
    const wrapper = mount(RuntimeQueuePanel, { props: { initialJobId: 'old-job' } })
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledWith('/runtime/jobs/old-job')
    const focused = wrapper.get('[aria-label="定位的任务"]')
    expect(focused.text()).toContain('发布给品牌号')
    expect(focused.text()).not.toContain('重试')
    await focused.findAll('button').find(button => button.text() === '查看详情')!.trigger('click')
    expect(mocks.push).toHaveBeenCalledWith('/content-publish?task=publish-1')
    wrapper.unmount()
  })

  it('切换筛选后忽略较慢的旧队列响应', async () => {
    let finish!: (value: any) => void
    mocks.get.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const wrapper = mount(RuntimeQueuePanel)
    await flushPromises()
    mocks.get.mockResolvedValue({ data: { items: [{ id: 'new', kind: 'ai_job', status: 'failed', display_name: '当前筛选结果' }], total: 1, active: {} } })
    await wrapper.get('select').setValue('failed')
    await flushPromises()
    finish({ data: { items: [{ id: 'old', kind: 'crawl_task', status: 'queued', display_name: '旧筛选结果' }], total: 1, active: {} } })
    await flushPromises()
    expect(wrapper.text()).toContain('当前筛选结果')
    expect(wrapper.text()).not.toContain('旧筛选结果')
    wrapper.unmount()
  })
})
