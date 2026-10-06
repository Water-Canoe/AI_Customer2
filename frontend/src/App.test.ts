import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.vue'
import { routes } from './router'

const mocks = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn(), confirm: vi.fn() }))
vi.mock('./shared/api', () => ({ api: { get: mocks.get, put: mocks.put, post: mocks.post } }))
vi.mock('element-plus', async importOriginal => ({
  ...await importOriginal<typeof import('element-plus')>(),
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: mocks.confirm },
}))

// 保留真实路由、业务页面和离页保护；仅用普通容器替换组件库外壳。
const container = defineComponent({ setup: (_, { slots }) => () => h('div', slots.default?.()) })
const overlay = defineComponent({ props: { modelValue: Boolean }, setup: (props, { slots }) => () => props.modelValue ? h('div', slots.default?.()) : null })
let wrapper: ReturnType<typeof mount> | undefined
async function open(path: string) {
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(path)
  wrapper = mount(App, { global: {
    plugins: [router],
    stubs: {
      ...Object.fromEntries(['ElContainer', 'ElAside', 'ElHeader', 'ElMain', 'ElMenu', 'ElMenuItem', 'ElSubMenu', 'ElIcon', 'ElButton', 'ElDropdown', 'ElDropdownMenu', 'ElDropdownItem'].map(name => [name, container])),
      ElDrawer: overlay, ElDialog: overlay, ElPagination: true,
    },
  } })
  await flushPromises()
  return { router, view: wrapper }
}
async function tab(label: string) {
  await wrapper!.findAll('nav[aria-label="获客工作区"] button').find(button => button.text() === label)!.trigger('click')
  await flushPromises()
}

describe('获客统一工作区', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.get.mockImplementation(async (url: string, options: any) => {
      if (url === '/tasks') return { data: { items: [{ id: 'task-1', name: '采集任务一', status: 'completed' }], total: 80, total_pages: 4, ...options.params } }
      if (url === '/tasks/task-1') return { data: { id: 'task-1', name: '采集任务一', status: 'completed', logs: [] } }
      if (url === '/ai/workbench') return { data: { items: [{ id: 1, target_type: options.params.tab === 'leads' ? 'lead' : 'competitor', nickname: '候选对象' }], total: 80, total_pages: 4 } }
      if (url === '/ai/jobs/history-job') return { data: { id: 'history-job', target_name: '历史客户', target_type: 'lead', status: 'succeeded', input_payload: {}, output_payload: {} } }
      if (url === '/overview/tree') return { data: [{ id: 'platform:dy', kind: 'platform', label: '抖音', has_children: true, child_count: 11, metrics: {} }] }
      if (url === '/overview/children') return { data: { items: [], total: 11, total_pages: 2, page_size: 10, page: options.params.page } }
      if (url === '/settings') return { data: { ai_base_url: 'https://example.test', icp_profile: {} } }
      return { data: {} }
    })
  })
  afterEach(() => { wrapper?.unmount(); wrapper = undefined })

  it('keeps one navigation and restores each tab query, task detail, and browser history', async () => {
    const { router, view } = await open('/acquisition?query=设备&page=2&page_size=20&task=task-1')
    const navigation = view.get('nav[aria-label="获客工作区"]').element
    expect(view.get('h1').text()).toBe('获客工作台')
    expect(view.get('.nav').attributes('default-active')).toBe('acquisition')
    expect(mocks.get).toHaveBeenCalledWith('/tasks', { params: { query: '设备', page: 2, page_size: 20 } })
    await tab('AI筛选')
    await router.replace('/ai?tab=leads&keyword=报价&page=2&page_size=20&job=history-job')
    await flushPromises()
    await tab('来源与证据')
    await router.replace('/overview?job=unrelated-job&tab=history')
    await flushPromises()
    expect(mocks.get).not.toHaveBeenCalledWith('/ai/jobs/unrelated-job')
    expect(view.text()).not.toContain('历史客户')
    await tab('AI筛选')
    expect(router.currentRoute.value.query).toMatchObject({ keyword: '报价', page: '2', job: 'history-job' })
    expect(view.text()).toContain('历史客户')
    await tab('采集与结果')
    expect(view.get('nav[aria-label="获客工作区"]').element).toBe(navigation)
    expect(router.currentRoute.value.query).toMatchObject({ query: '设备', page: '2', page_size: '20', task: 'task-1' })
    expect((view.get('.task-list-tools input').element as HTMLInputElement).value).toBe('设备')
    expect(view.get('.operations-detail').text()).toContain('task-1')
    await router.push('/acquisition?query=新条件&page=3&page_size=20')
    await flushPromises()
    router.back()
    await flushPromises()
    expect((view.get('.task-list-tools input').element as HTMLInputElement).value).toBe('设备')
    expect(mocks.get.mock.calls.filter(([url]) => url === '/tasks').slice(-1)[0]).toEqual(['/tasks', { params: { query: '设备', page: 2, page_size: 20 } }])
    router.forward()
    await flushPromises()
    expect((view.get('.task-list-tools input').element as HTMLInputElement).value).toBe('新条件')
  })
  it('retains expanded evidence and its child page when switching tabs', async () => {
    const { view } = await open('/overview')
    await view.get('.overview-row .outline-button').trigger('click')
    await flushPromises()
    await view.findAll('button').find(button => button.text() === '下一页')!.trigger('click')
    await flushPromises()
    await tab('采集与结果')
    await tab('来源与证据')
    expect(view.get('.overview-row .outline-button').text()).toBe('收起')
    expect(view.text()).toContain('2 / 2')
    expect(mocks.get).toHaveBeenLastCalledWith('/overview/children', { params: { node_id: 'platform:dy', page: 2, page_size: 10 } })
  })
  it('keeps edited rules when a tab switch is cancelled and never saves them silently', async () => {
    const { router, view } = await open('/settings')
    await view.get('input[type="text"]').setValue('https://draft.test')
    mocks.confirm.mockRejectedValueOnce('cancel')
    await tab('AI筛选')
    expect(router.currentRoute.value.name).toBe('settings')
    expect((view.get('input[type="text"]').element as HTMLInputElement).value).toBe('https://draft.test')
    mocks.confirm.mockResolvedValueOnce('confirm')
    await tab('AI筛选')
    expect(router.currentRoute.value.name).toBe('ai')
    expect(mocks.put).not.toHaveBeenCalled()
    expect(view.get('nav .is-active').text()).toBe('AI筛选')
  })
})
