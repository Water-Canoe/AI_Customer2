import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import type { Component } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ContentWorkbenchPage from './ContentWorkbenchPage'
import GlobalSettingsPage from './GlobalSettingsPage'
import PublishCenterPage from './PublishCenterPage'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  route: { name: '', fullPath: '', query: {} },
}))
vi.mock('../shared/api', () => ({ api: { get: mocks.get } }))
vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
  ElPagination: { render: () => null },
}))

const pages = [
  { label: '内容记录', component: ContentWorkbenchPage, view: 'content-records', pollUrl: '/content/publish-tasks' },
  { label: '发布中心', component: PublishCenterPage, view: 'content-publish', pollUrl: '/accounts' },
  { label: '全局设置', component: GlobalSettingsPage, view: 'global-settings', pollUrl: '/accounts' },
]

// 仅提供活动任务与正在检查的账号；测试不会调用登录、发布等业务动作。
function response(url: string) {
  return { data: url === '/accounts'
    ? [{ id: 'account', features: [], default_features: [], login_status: { user: { status: 'checking' }, creator: { status: 'checking' } } }]
    : { items: [], active: 1, summary: { active: 1 }, page: 1, page_size: 20, total: 0 } }
}

describe('局部后台轮询', () => {
  let wrapper: VueWrapper | undefined

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-03T00:00:00Z'))
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    mocks.route.query = {}
    mocks.get.mockImplementation(async (url: string) => response(url))
  })
  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.clearAllMocks()
    vi.useRealTimers()
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  })

  it.each(pages)('$label 隐藏时暂停、恢复时刷新，慢请求期间不重复查询，卸载后停止', async ({ component, view, pollUrl }) => {
    mocks.route.name = view
    wrapper = mount(component as Component)
    await flushPromises()
    mocks.get.mockClear()

    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    await vi.advanceTimersByTimeAsync(6000)
    expect(mocks.get).not.toHaveBeenCalled()

    let finish!: (value: ReturnType<typeof response>) => void
    mocks.get.mockImplementation((url: string) => url === pollUrl
      ? new Promise(resolve => { finish = resolve })
      : Promise.resolve(response(url)))
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    await vi.advanceTimersByTimeAsync(9000)
    expect(mocks.get.mock.calls.filter(([url]) => url === pollUrl)).toHaveLength(1)

    mocks.get.mockImplementation(async (url: string) => response(url))
    finish(response(pollUrl))
    await flushPromises()
    await vi.advanceTimersByTimeAsync(3000)
    expect(mocks.get.mock.calls.filter(([url]) => url === pollUrl)).toHaveLength(2)

    wrapper.unmount()
    wrapper = undefined
    mocks.get.mockClear()
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(6000)
    expect(mocks.get).not.toHaveBeenCalled()
  })

  it.each(pages)('$label 首次慢请求期间不叠加轮询，卸载后不会遗留定时器', async ({ component, view, pollUrl }) => {
    mocks.route.name = view
    if (view === 'content-publish') mocks.route.query = { video_job_id: 'video' }
    let finish!: (value: ReturnType<typeof response>) => void
    mocks.get.mockImplementation((url: string) => url === pollUrl
      ? new Promise(resolve => { finish = resolve })
      : Promise.resolve(response(url)))
    wrapper = mount(component as Component)
    await vi.advanceTimersByTimeAsync(9000)
    expect(mocks.get.mock.calls.filter(([url]) => url === pollUrl)).toHaveLength(1)
    const requestCount = mocks.get.mock.calls.length

    wrapper.unmount()
    wrapper = undefined
    finish(response(pollUrl))
    await flushPromises()
    await vi.advanceTimersByTimeAsync(6000)
    expect(mocks.get.mock.calls.filter(([url]) => url === pollUrl)).toHaveLength(1)
    expect(mocks.get).toHaveBeenCalledTimes(requestCount)
  })

  it('defers a hidden publish composer until accounts load and preserves its edited draft', async () => {
    mocks.route.name = 'content-publish'
    mocks.route.query = { video_job_id: 'video' }
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    mocks.get.mockImplementation(async (url: string) => {
      if (url === '/accounts') return { data: [{ id: 'account', name: '发布账号', enabled: true, features: ['publish'], default_features: ['publish'], feature_status: { publish: { status: 'ready' } } }] }
      if (url === '/content/video-jobs/video') return { data: { subject: '原始标题', script: '正文' } }
      return response(url)
    })
    wrapper = mount(PublishCenterPage)
    await flushPromises()
    expect(mocks.get).not.toHaveBeenCalled()

    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect((wrapper.get('.publish-account-options input').element as HTMLInputElement).checked).toBe(true)
    const title = wrapper.get('.publish-composer > label > input')
    await title.setValue('人工编辑标题')
    await wrapper.setProps({ refreshSeq: 1 })
    await flushPromises()
    expect((title.element as HTMLInputElement).value).toBe('人工编辑标题')
    expect(mocks.get.mock.calls.filter(([url]) => url === '/content/video-jobs/video')).toHaveLength(1)
  })
})
