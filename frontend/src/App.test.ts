import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App.vue'
import { routes } from './router'
import { ListPagination } from './components/ui/ListPagination'

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
  await wrapper!.findAll('nav.workspace-tabs button').find(button => button.text() === label)!.trigger('click')
  await vi.dynamicImportSettled()
  await flushPromises()
}

describe('统一业务工作区', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.get.mockImplementation(async (url: string, options: any) => {
      if (url === '/tasks') return { data: { items: [{ id: 'task-1', name: '采集任务一', status: 'completed' }], total: 80, total_pages: 4, ...options.params } }
      if (url === '/tasks/task-1') return { data: { id: 'task-1', name: '采集任务一', status: 'completed', logs: [] } }
      if (url === '/ai/workbench') return { data: { items: [{ id: 1, target_type: options.params.tab === 'leads' ? 'lead' : 'competitor', nickname: '候选对象' }], total: 80, total_pages: 4 } }
      if (url === '/ai/jobs/history-job') return { data: { id: 'history-job', target_name: '历史客户', target_type: 'lead', status: 'succeeded', input_payload: {}, output_payload: {} } }
      if (url === '/overview/tree') return { data: [{ id: 'platform:dy', kind: 'platform', label: '抖音', has_children: true, child_count: 11, metrics: {} }] }
      if (url === '/overview/children') return { data: { items: [], total: 11, total_pages: 2, page_size: 10, page: options.params.page } }
      if (url === '/settings') return { data: { ai_base_url: 'https://example.test', icp_profile: {}, dm_script_mode: 'fixed', fixed_dm_script: '已保存话术' } }
      if (url === '/accounts') return { data: ['account-1', 'account-2'].map(id => ({ id, platform: 'dy', name: id, enabled: true, features: ['message'], feature_status: { message: { status: 'ready' } } })) }
      if (url === '/message-inbox/conversations') return { data: [{ id: 12, account_id: options.params.account_id, nickname: '聊天客户', peer_user_id: 'peer', unread_count: 0, lead_id: null }] }
      if (url.startsWith('/message-inbox/accounts/')) return { data: { state: 'disconnected', receive_ready: true, send_ready: true, error: '', history_scope: 'local' } }
      if (url === '/message-inbox/conversations/12/messages') return { data: { messages: [], has_more: false } }
      if (url === '/message-workbench/keywords') return { data: [{ platform: 'dy', keyword: '设备', label: '设备' }] }
      if (url === '/message-workbench/customers') return { data: { rows: [{ lead_id: 77, nickname: '客户七十七', platform: 'dy', follow_status: '未私信' }], total: 60, ...options.params } }
      if (url.startsWith('/message-workbench/customers/')) return { data: {
        customer: { lead_id: Number(url.split('/').pop()), nickname: '客户七十七', platform: 'dy' },
        sources: [{ keyword: '设备', comment_text: '需要设备' }],
        events: Array.from({ length: 11 }, (_, i) => ({ note: `跟进记录${i + 1}`, from_status: '未私信', to_status: '已回复' })),
      } }
      if (url === '/message-workbench/auto-message-batches') {
        const batch = { id: options.params.batch_id || 'recent', status: 'succeeded', account_name: '实际执行账号' }
        return { data: { batches: [batch], selected_batch: batch, selected_batch_id: batch.id, total: 40, item_total: 100, ...options.params, items: [{ lead_account_id: 77, nickname: '本次客户', status: 'succeeded' }] } }
      }
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
  it('retains customer filters, detail history page and account choice across the shared tabs', async () => {
    const { router, view } = await open('/message-workbench?customer=77&keyword=设备&platform=dy&intention=高&status=全部&query=报价&page=2&page_size=10')
    const navigation = view.get('nav[aria-label="客户工作区"]').element
    expect(view.get('h1').text()).toBe('客户工作台')
    expect(view.get('.nav').attributes('default-active')).toBe('message-workbench')
    expect(view.findAll('nav button').map(button => button.text())).toEqual(['客户跟进', '私信收件箱', '触达记录', '私信设置'])
    await view.get('.auto-message-account select').setValue('account-2')
    await view.findAll('button').find(button => button.text().startsWith('跟进历史'))!.trigger('click')
    view.get('.message-detail-drawer').findComponent(ListPagination).vm.$emit('change', { page: 2, page_size: 10 })
    await flushPromises()
    expect(view.get('.event-timeline').text()).toContain('跟进记录11')
    await tab('私信设置')
    expect(view.get('nav[aria-label="客户工作区"]').element).toBe(navigation)
    expect(view.element.querySelector('.message-detail-drawer')).toBeNull()
    await tab('触达记录')
    await router.replace('/message-batches?customer=999&keyword=其它来源')
    await flushPromises()
    expect(mocks.get).not.toHaveBeenCalledWith('/message-workbench/customers/999')
    expect(mocks.get).not.toHaveBeenCalledWith('/message-workbench/customers', { params: expect.objectContaining({ keyword: '其它来源' }) })
    await tab('客户跟进')
    expect(router.currentRoute.value.query).toMatchObject({ customer: '77', keyword: '设备', intention: '高', query: '报价', page: '2', page_size: '10' })
    expect((view.get('.message-search input').element as HTMLInputElement).value).toBe('报价')
    expect(view.get('.event-timeline').text()).toBe('未私信 -> 已回复跟进记录11')
    expect((view.get('.auto-message-account select').element as HTMLSelectElement).value).toBe('account-2')
    expect(mocks.get).toHaveBeenCalledWith('/message-workbench/customers', { params: expect.objectContaining({ intention: '高', page: 2, page_size: 10, keyword: '设备' }) })
    await router.push('/message-workbench?status=待私信')
    await flushPromises()
    expect(view.element.querySelector('.message-detail-drawer')).toBeNull()
    router.back()
    await flushPromises()
    expect(router.currentRoute.value.query.customer).toBe('77')
    expect(view.element.querySelector('.message-detail-drawer')).not.toBeNull()
    expect(mocks.post).not.toHaveBeenCalled()
  })
  it('restores batch and item pages with history and returns from a customer to the exact batch', async () => {
    const { router, view } = await open('/message-batches?batch=history&page=2&page_size=20&item_page=3&item_page_size=10')
    const params = { batch_id: 'history', page: 2, page_size: 20, item_page: 3, item_page_size: 10 }
    expect(mocks.get).toHaveBeenCalledWith('/message-workbench/auto-message-batches', { params })
    await router.push({ name: 'message-batches', query: { ...router.currentRoute.value.query, item_page: 4, item_page_size: 20 } })
    await flushPromises()
    expect(mocks.get).toHaveBeenLastCalledWith('/message-workbench/auto-message-batches', { params: { ...params, item_page: 4, item_page_size: 20 } })
    router.back()
    await flushPromises()
    expect(mocks.get).toHaveBeenLastCalledWith('/message-workbench/auto-message-batches', { params })
    const batchRequests = () => mocks.get.mock.calls.filter(([url]) => url === '/message-workbench/auto-message-batches').length
    const beforePaging = batchRequests()
    view.get('.message-batch-items-panel').findComponent(ListPagination).vm.$emit('change', { page: 4, page_size: 20 })
    await flushPromises()
    expect(batchRequests()).toBe(beforePaging + 1)
    expect(mocks.get).toHaveBeenLastCalledWith('/message-workbench/auto-message-batches', { params: { ...params, item_page: 4, item_page_size: 20 } })
    await router.replace({ name: 'message-batches', query: { batch: 'history', page: 2, page_size: 20, item_page: 3, item_page_size: 10 } })
    await flushPromises()
    await view.findAll('button').find(button => button.text() === '查看客户跟进')!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.customer).toBe('77')
    await tab('私信设置')
    await tab('客户跟进')
    await view.findAll('button').find(button => button.text() === '返回原批次')!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ batch: 'history', page: '2', page_size: '20', item_page: '3', item_page_size: '10' })
    expect(view.text()).toContain('本次客户')
    await tab('私信设置')
    await tab('触达记录')
    expect(router.currentRoute.value.query.item_page).toBe('3')
    expect(view.get('h1').text()).toBe('客户工作台')
    expect(view.get('.nav').attributes('default-active')).toBe('message-workbench')
  })
  it('protects unsaved message settings when switching tabs and discards only after confirmation', async () => {
    const { router, view } = await open('/message-settings')
    await view.get('textarea').setValue('尚未保存的话术')
    mocks.confirm.mockRejectedValueOnce('cancel')
    await tab('客户跟进')
    expect(router.currentRoute.value.name).toBe('message-settings')
    expect((view.get('textarea').element as HTMLTextAreaElement).value).toBe('尚未保存的话术')
    mocks.confirm.mockResolvedValueOnce('confirm')
    await tab('客户跟进')
    await tab('私信设置')
    expect((view.get('textarea').element as HTMLTextAreaElement).value).toBe('已保存话术')
    expect(mocks.put).not.toHaveBeenCalled()
  })
  it('keeps the inbox selection and unsent text when switching customer tabs without connecting', async () => {
    const { router, view } = await open('/message-inbox?account_id=account-2&conversation=12')
    const navigation = view.get('nav[aria-label="客户工作区"]').element
    await view.get('.inbox-composer textarea').setValue('未发送的聊天草稿')
    await tab('客户跟进')
    await tab('私信收件箱')
    expect(view.get('nav[aria-label="客户工作区"]').element).toBe(navigation)
    expect(router.currentRoute.value.query).toEqual({ account_id: 'account-2', conversation: '12' })
    expect((view.get('.inbox-composer textarea').element as HTMLTextAreaElement).value).toBe('未发送的聊天草稿')
    expect(mocks.post).not.toHaveBeenCalled()
  })
})
