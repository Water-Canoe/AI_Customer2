import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import MessageWorkbenchPage from './MessageWorkbenchPage'
import { ref } from 'vue'
import { useMessageWorkbench } from '../composables/messageWorkbench'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), push: vi.fn(), replace: vi.fn(), route: { name: 'message-workbench', query: { customer: '7', status: '全部' } as Record<string, string>, fullPath: '/message-workbench?customer=7&status=全部' } }))
vi.mock('../shared/api', () => ({ api: { get: mocks.get, post: mocks.post } }))
vi.mock('vue-router', () => ({ useRoute: () => mocks.route, useRouter: () => mocks }))
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  return { ElPagination: { render: () => null }, ElMessage: { error: vi.fn() }, ElDrawer: defineComponent({ props: { modelValue: Boolean }, setup: (props, { slots }) => () => props.modelValue ? h('div', slots.default?.()) : null }) }
})

describe('客户跟进交互', () => {
  it('opens and automatically messages XHS customers using an XHS account', async () => {
    mocks.get.mockResolvedValue({ data: ['dy', 'xhs'].map(platform => ({ id: platform + '-brand', platform, enabled: true, features: ['message'], feature_status: { message: { status: 'ready' } } })) })
    mocks.post.mockResolvedValue({ data: { id: 22 } })
    const customer = { lead_id: 8, nickname: '小红书客户', platform: 'xhs', script: '小红书话术' }
    const wrapper = mount(MessageWorkbenchPage, { props: { filters: { platform: 'xhs', keyword: '来源A' }, detail: { customer }, customers: { rows: [customer], total: 1 } } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '打开私信会话')!.trigger('click')
    await flushPromises()
    expect(mocks.post).toHaveBeenCalledWith('/message-inbox/conversations', { account_id: 'xhs-brand', lead_id: 8 })
    expect(mocks.push).toHaveBeenCalledWith({ path: '/message-inbox', query: { platform: 'xhs', account_id: 'xhs-brand', conversation: '22' } })
    await wrapper.findAll('button').find(button => button.text() === '准备私信')!.trigger('click')
    expect(wrapper.emitted('message-customer')?.[0][0]).toEqual({ ...customer, account_id: 'xhs-brand' })
    await wrapper.findAll('button').find(button => button.text() === '自动私信')!.trigger('click')
    expect(wrapper.emitted('auto-message-customer')?.[0][0]).toEqual({ ...customer, account_id: 'xhs-brand' })
    expect(wrapper.get('.auto-message-batch-panel').findAll('option').some(item => item.attributes('value') === 'xhs-brand')).toBe(true)
    wrapper.unmount()
  })
  beforeEach(() => { vi.clearAllMocks(); mocks.route.query = { customer: '7', status: '全部' }; mocks.get.mockResolvedValue({ data: [] }) })
  it('changes batch accounts with the source platform and keeps Kuaishou disabled', async () => {
    mocks.get.mockResolvedValue({ data: ['dy', 'xhs'].map(platform => ({ id: platform + '-brand', platform, name: platform + '账号', enabled: true, features: ['message'], feature_status: { message: { status: 'ready' } } })) })
    const wrapper = mount(MessageWorkbenchPage, { props: { filters: { platform: 'dy', keyword: '来源A' } } })
    await flushPromises()
    const batchPanel = wrapper.get('.auto-message-batch-panel')
    expect((batchPanel.get('select').element as HTMLSelectElement).value).toBe('dy-brand')
    await wrapper.setProps({ filters: { platform: 'xhs', keyword: '来源B' } })
    expect((batchPanel.get('select').element as HTMLSelectElement).value).toBe('xhs-brand')
    expect(batchPanel.findAll('option').some(option => option.attributes('value') === 'dy-brand')).toBe(false)
    await batchPanel.findAll('button').find(button => button.text() === 'AI一键私信')!.trigger('click')
    expect(wrapper.emitted('start-auto-message-batch')?.[0][0]).toMatchObject({ platform: 'xhs', keyword: '来源B', account_id: 'xhs-brand' })
    await wrapper.setProps({ filters: { platform: 'ks', keyword: '来源C' } })
    expect(batchPanel.findAll('button').find(button => button.text() === 'AI一键私信')!.attributes('disabled')).toBeDefined()
    expect((batchPanel.get('select').element as HTMLSelectElement).value).toBe('')
    wrapper.unmount()
  })
  it('locates the customer, separates preparation from confirmation and keeps source and history views', async () => {
    mocks.get.mockResolvedValue({ data: [{ id: 'brand', platform: 'dy', enabled: true, features: ['message'], feature_status: { message: { status: 'ready' } } }] })
    const customer = { lead_id: 7, nickname: '客户A', platform: 'dy', follow_status: '未私信', script: '问候语', profile_url: 'https://example.com/customer' }
    const wrapper = mount(MessageWorkbenchPage, { props: { customers: { rows: [customer], page: 1, total: 1 } } })
    await flushPromises()
    expect(wrapper.emitted('select-customer')?.[0][0]).toBe('7')
    await wrapper.setProps({ detail: { customer, sources: [{ keyword: '企业获客', comment_text: '需要客服' }], events: [{ from_status: '待筛选', to_status: '未私信', note: '已判定为目标客户' }] } })
    expect(wrapper.emitted('filter-change')?.[0][0]).toMatchObject({ status: '全部' })
    await wrapper.findAll('button').find(button => button.text() === '准备私信')!.trigger('click')
    expect(wrapper.emitted('message-customer')?.[0][0]).toEqual({ ...customer, account_id: 'brand' })
    expect(wrapper.emitted('confirm-message-customer')).toBeUndefined()
    await wrapper.findAll('button').find(button => button.text() === '确认已发送')!.trigger('click')
    expect(wrapper.emitted('confirm-message-customer')?.[0][0]).toEqual(customer)
    expect(wrapper.text()).toContain('需要客服')
    await wrapper.findAll('button').find(button => button.text().startsWith('跟进历史'))!.trigger('click')
    expect(wrapper.text()).toContain('已判定为目标客户')
    wrapper.unmount()
  })
  it('restores an exact intention filter and sends the same conditions with pagination', async () => {
    mocks.route.query = { intention: '高', status: '待私信', keyword: '来源A', page: '2', page_size: '10' }
    mocks.get.mockImplementation(async (url: string) => ({ data: url === '/message-workbench/customers' ? { rows: [], total: 2, page: 2 } : [] }))
    const workbench = useMessageWorkbench({ router: { push: mocks.push }, settings: ref({}), refreshRelated: async () => undefined })
    const wrapper = mount(MessageWorkbenchPage)
    const restored = wrapper.emitted('filter-change')![0][0] as Record<string, unknown>
    await workbench.changeFilter(restored)
    expect(mocks.get).toHaveBeenCalledWith('/message-workbench/customers', { params: expect.objectContaining({ intention: '高', keyword: '来源A', page: 2, page_size: 10 }) })
    expect(mocks.get).toHaveBeenCalledWith('/message-workbench/keywords', { params: { intention: '高' } })
    await wrapper.setProps({ filters: workbench.filters.value })
    expect((wrapper.get('[aria-label="客户意向"]').element as HTMLSelectElement).value).toBe('高')
    await wrapper.get('[aria-label="客户意向"]').setValue('低')
    const changes = wrapper.emitted('filter-change')!
    const changed = changes[changes.length - 1][0] as Record<string, unknown>
    await workbench.changeFilter(changed)
    expect(mocks.get).toHaveBeenCalledWith('/message-workbench/customers', { params: expect.objectContaining({ intention: '低', page: 1 }) })
    expect(mocks.replace).toHaveBeenCalledWith({ path: '/message-workbench', query: expect.objectContaining({ intention: '低', keyword: '来源A', page: 1 }) })
    wrapper.unmount()
  })
  it('explains the full source batch scope independently of the filtered customer list', async () => {
    mocks.route.query = {}
    localStorage.removeItem('message-workbench-auto-batch-config')
    mocks.get.mockResolvedValue({ data: [{ id: 'account-1', platform: 'dy', name: '私信账号', enabled: true, features: ['message'], feature_status: { message: { status: 'ready' } } }] })
    const wrapper = mount(MessageWorkbenchPage, { props: {
      filters: { platform: 'dy', keyword: '来源A', intention: '高', query: '文字搜索', status: '已回复', page: 3, page_size: 10 },
      customers: { rows: [], total: 0, page: 3, page_size: 10 }
    } })
    await flushPromises()
    const batchPanel = wrapper.get('.auto-message-batch-panel')
    expect(batchPanel.text()).toContain('抖音 / 来源A')
    expect(batchPanel.text()).toContain('全部未私信、话术可用的目标客户')
    expect(batchPanel.text()).toContain('按评论/来源时间倒序选取最多 10 人')
    expect(batchPanel.text()).toContain('当前列表的意向、文字搜索、状态和分页不参与批次筛选')
    await batchPanel.findAll('input[type="number"]')[0].setValue(7)
    expect(batchPanel.text()).toContain('选取最多 7 人')
    await batchPanel.findAll('button').find(button => button.text() === 'AI一键私信')!.trigger('click')
    expect(wrapper.emitted('start-auto-message-batch')?.[0][0]).toEqual({ platform: 'dy', keyword: '来源A', count: 7, interval_min_seconds: 5, interval_max_seconds: 20, account_id: 'account-1' })
    expect(mocks.get).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    localStorage.removeItem('message-workbench-auto-batch-config')
  })
  it('opens a local conversation from customer detail without executing a send', async () => {
    mocks.get.mockResolvedValue({ data: [{ id: 'brand', platform: 'dy', name: '品牌号', enabled: true, features: ['message'], feature_status: { message: { status: 'ready' } } }] })
    mocks.post.mockResolvedValue({ data: { id: 12 } })
    const wrapper = mount(MessageWorkbenchPage, { props: { detail: { customer: { lead_id: 7, nickname: '客户A', platform: 'dy' } } } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '打开私信会话')!.trigger('click')
    await flushPromises()
    expect(mocks.post).toHaveBeenCalledTimes(1)
    expect(mocks.post).toHaveBeenCalledWith('/message-inbox/conversations', { account_id: 'brand', lead_id: 7 })
    expect(mocks.push).toHaveBeenCalledWith({ path: '/message-inbox', query: { account_id: 'brand', platform: 'dy', conversation: '12' } })
    wrapper.unmount()
  })
})
