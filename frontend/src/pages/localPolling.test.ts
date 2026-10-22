import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import type { Component } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ContentWorkbenchPage from './ContentWorkbenchPage'
import GlobalSettingsPage from './GlobalSettingsPage'
import PublishCenterPage from './PublishCenterPage'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  push: vi.fn(),
  route: { name: '', fullPath: '', query: {} },
}))
vi.mock('../shared/api', () => ({ api: { get: mocks.get, put: mocks.put, delete: mocks.delete } }))
vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ push: mocks.push, replace: vi.fn() }),
  onBeforeRouteLeave: vi.fn(),
  onBeforeRouteUpdate: vi.fn(),
}))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
  ElPagination: { render: () => null },
}))

const pages = [
  { label: '内容作品库', component: ContentWorkbenchPage, view: 'content-records', pollUrl: '/content/outputs' },
  { label: '发布中心', component: PublishCenterPage, view: 'content-publish', pollUrl: '/accounts' },
  { label: '账号中心', component: GlobalSettingsPage, view: 'accounts', pollUrl: '/accounts' },
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
    mocks.put.mockResolvedValue({ data: { exists: true, updated_at: null } })
    mocks.delete.mockResolvedValue({ data: {} })
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
    if (view === 'content-publish') mocks.route.query = { output_ids: 'output' }
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
    mocks.route.query = { output_ids: 'output' }
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    mocks.get.mockImplementation(async (url: string) => {
      if (url === '/accounts') return { data: [{ id: 'account', name: '发布账号', enabled: true, features: ['publish'], default_features: ['publish'], feature_status: { publish: { status: 'ready' } } }] }
      if (url === '/content/outputs/output') return { data: { id: 'output', subject: '原始标题', script: '正文' } }
      return response(url)
    })
    wrapper = mount(PublishCenterPage)
    await flushPromises()
    expect(mocks.get).not.toHaveBeenCalled()

    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect((wrapper.get('.publish-account-options input').element as HTMLInputElement).checked).toBe(true)
    const title = wrapper.get('.publish-composer .publish-field input')
    await title.setValue('人工编辑标题')
    await wrapper.setProps({ refreshSeq: 1 })
    await flushPromises()
    expect((title.element as HTMLInputElement).value).toBe('人工编辑标题')
    expect(mocks.get.mock.calls.filter(([url]) => url === '/content/outputs/output')).toHaveLength(1)
  })

  it('账号中心直接展示账号，并保留返回工作中的查询参数', async () => {
    mocks.route.name = 'accounts'
    mocks.route.query = { feature: 'acquisition', return_to: '/acquisition?create=1' }
    wrapper = mount(GlobalSettingsPage)
    await flushPromises()
    expect(wrapper.find('.global-settings-tabs').exists()).toBe(false)
    expect(wrapper.find('.account-create-pane').exists()).toBe(true)
    await wrapper.get('.account-return').trigger('click')
    expect(mocks.push).toHaveBeenCalledWith('/acquisition?create=1')
  })

  it('系统设置不读取平台账号，也不提供外部返回地址', async () => {
    mocks.route.name = 'global-settings'
    mocks.route.query = { return_to: '//external.example' }
    wrapper = mount(GlobalSettingsPage)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(6000)
    expect(mocks.get).not.toHaveBeenCalled()
    expect(wrapper.find('.account-return').exists()).toBe(false)
    expect(wrapper.get('.global-settings-tabs').text()).not.toContain('平台账号')
  })

  it('账号仍在线时也跟进排队登录任务后续产生的状态变化', async () => {
    mocks.route.name = 'accounts'
    let status = 'ready'
    mocks.get.mockImplementation(async () => ({ data: [{ id: 'account', name: '在线账号', role: 'service', features: ['message'], default_features: [], login_status: { user: { available: true, status } } }] }))
    wrapper = mount(GlobalSettingsPage)
    await flushPromises()
    expect(wrapper.text()).toContain('已登录')
    status = 'checking'
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(wrapper.text()).toContain('检查中')
    expect(mocks.get.mock.calls.filter(([url]) => url === '/accounts')).toHaveLength(2)
  })
})
