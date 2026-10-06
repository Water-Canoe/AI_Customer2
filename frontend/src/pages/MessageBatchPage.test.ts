import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import MessageBatchPage from './MessageBatchPage'

const mocks = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: { batch: 'history', page: '2', item_page: '3' } }), useRouter: () => mocks }))
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  return { ElPagination: { render: () => null }, ElDrawer: defineComponent({ props: { modelValue: Boolean }, setup: (props, { slots }) => () => props.modelValue ? h('div', slots.default?.()) : null }) }
})

describe('触达历史返回上下文', () => {
  it('shows the saved execution account and links the exact customer back to the same batch pages', async () => {
    const batch = { id: 'history', status: 'succeeded', account_id: 'old-account', account_name: '当时执行账号' }
    const wrapper = mount(MessageBatchPage, { props: { batches: { batches: [batch], selected_batch: batch, selected_batch_id: '', page: 2, page_size: 10, item_page: 3, item_page_size: 20, items: [{ lead_account_id: 77, nickname: '本次客户', status: 'succeeded', script: '本次话术' }] } } })
    await flushPromises()
    expect(wrapper.emitted('select-auto-message-batch')![0][0]).toMatchObject({ id: 'history', page: 2, item_page: 3 })
    expect(wrapper.text()).toContain('当时执行账号 (old-account)')
    await wrapper.findAll('button').find(button => button.text() === '查看客户跟进')!.trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({ path: '/message-workbench', query: { customer: '77', status: '全部', return_to: '/message-batches?batch=history&page=2&page_size=10&item_page=3&item_page_size=20' } })
    wrapper.unmount()
  })
})
