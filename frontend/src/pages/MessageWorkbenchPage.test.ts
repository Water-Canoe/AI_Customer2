import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import MessageWorkbenchPage from './MessageWorkbenchPage'

const mocks = vi.hoisted(() => ({ get: vi.fn(), push: vi.fn(), replace: vi.fn() }))
vi.mock('../shared/api', () => ({ api: { get: mocks.get } }))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: { customer: '7', status: '全部' }, fullPath: '/message-workbench?customer=7&status=全部' }), useRouter: () => mocks }))
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  return { ElPagination: { render: () => null }, ElMessage: { error: vi.fn() }, ElDrawer: defineComponent({ props: { modelValue: Boolean }, setup: (props, { slots }) => () => props.modelValue ? h('div', slots.default?.()) : null }) }
})

describe('客户跟进交互', () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.get.mockResolvedValue({ data: [] }) })
  it('locates the customer, separates preparation from confirmation and keeps source and history views', async () => {
    const customer = { lead_id: 7, nickname: '客户A', platform: 'dy', follow_status: '未私信', script: '问候语', profile_url: 'https://example.com/customer' }
    const wrapper = mount(MessageWorkbenchPage, { props: { customers: { rows: [customer], page: 1, total: 1 } } })
    await flushPromises()
    expect(wrapper.emitted('select-customer')?.[0][0]).toBe('7')
    await wrapper.setProps({ detail: { customer, sources: [{ keyword: '企业获客', comment_text: '需要客服' }], events: [{ from_status: '待筛选', to_status: '未私信', note: '已判定为目标客户' }] } })
    expect(wrapper.emitted('filter-change')?.[0][0]).toMatchObject({ status: '全部' })
    await wrapper.findAll('button').find(button => button.text() === '准备私信')!.trigger('click')
    expect(wrapper.emitted('message-customer')?.[0][0]).toEqual(customer)
    expect(wrapper.emitted('confirm-message-customer')).toBeUndefined()
    await wrapper.findAll('button').find(button => button.text() === '确认已发送')!.trigger('click')
    expect(wrapper.emitted('confirm-message-customer')?.[0][0]).toEqual(customer)
    expect(wrapper.text()).toContain('需要客服')
    await wrapper.findAll('button').find(button => button.text().startsWith('跟进历史'))!.trigger('click')
    expect(wrapper.text()).toContain('已判定为目标客户')
    wrapper.unmount()
  })
})
