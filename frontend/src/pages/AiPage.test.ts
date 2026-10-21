import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AiPage from './AiPage'

const mocks = vi.hoisted(() => ({ get: vi.fn(), replace: vi.fn(), push: vi.fn(), route: { name: 'ai', query: {} as Record<string, string> } }))
vi.mock('../shared/api', () => ({ api: { get: mocks.get } }))
vi.mock('vue-router', () => ({ useRoute: () => mocks.route, useRouter: () => mocks }))
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  return { ElPagination: { render: () => null }, ElDrawer: defineComponent({ props: { modelValue: Boolean }, setup: (props, { slots }) => () => props.modelValue ? h('div', slots.default?.()) : null }) }
})

describe('AI筛选查询上下文', () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.route.query = {} })
  it.each([['competitors', 'competitor', '非竞品'], ['leads', 'lead', '非客户']])('analyses all unanalysed %s while deletion still uses only the current page', async (tab, targetType, result) => {
    const wrapper = mount(AiPage, { props: {
      workbench: { items: [{ id: 7, target_type: targetType, analysis_status: '已分析', result_label: result }], total: 50 },
      query: { tab, keyword: '当前筛选', status: '已分析', result, page: 3, page_size: 10 },
    } })
    await wrapper.findAll('.ai-toolbar button').find(button => button.text() === '一键分析')!.trigger('click')
    expect(wrapper.emitted('create-batch-jobs')?.[0]).toEqual([targetType])
    await wrapper.findAll('.ai-toolbar button').find(button => button.text().startsWith('删除本页'))!.trigger('click')
    expect(wrapper.emitted(tab === 'leads' ? 'delete-non-customers' : 'delete-non-competitors')?.[0]).toEqual([[7]])
    wrapper.unmount()
  })
  it('rehydrates filters from the shared query and preserves the keyword when switching object type', async () => {
    const wrapper = mount(AiPage, { props: { workbench: { items: [], page: 1 }, query: { tab: 'competitors', keyword: '企业获客', status: '已分析', result: '竞品', page: 2, page_size: 20 } } })
    expect((wrapper.get('.ai-search input').element as HTMLInputElement).value).toBe('企业获客')
    await wrapper.findAll('.ai-tabs button').find(button => button.text() === '客户意向分析')!.trigger('click')
    expect(wrapper.emitted('query-change')?.[0][0]).toMatchObject({ tab: 'leads', keyword: '企业获客', status: '', result: '', page: 1, page_size: 20 })
    await wrapper.setProps({ query: { tab: 'leads', keyword: '当前条件', status: '失败', result: '', page: 1, page_size: 10 } })
    await flushPromises()
    expect((wrapper.get('.ai-search input').element as HTMLInputElement).value).toBe('当前条件')
    expect((wrapper.findAll('select')[0].element as HTMLSelectElement).value).toBe('失败')
    wrapper.unmount()
  })
  it.each(['competitors', 'leads'])('finds customers from all competitors on the %s tab without forwarding list filters', async tab => {
    const wrapper = mount(AiPage, { props: { workbench: { items: [], total: 250 }, query: { tab, keyword: '仅当前搜索', status: '失败', result: '非客户', page: 4, page_size: 10 } } })
    await wrapper.findAll('.ai-toolbar button').find(button => button.text() === '一键找客户')!.trigger('click')
    expect(wrapper.emitted('find-customers')).toEqual([[{ kind: 'all_competitors' }]])
    expect(wrapper.emitted('query-change')).toBeUndefined()
    wrapper.unmount()
  })
  it('continues a competitor to comment collection and a target customer to its follow-up detail', async () => {
    const wrapper = mount(AiPage, { props: { workbench: { items: [{ id: 9, target_type: 'competitor', result_label: '竞品', platform: 'dy', profile_url: 'https://example.com/competitor' }] }, query: { tab: 'competitors', keyword: '', page: 1, page_size: 10 } } })
    await wrapper.findAll('button').find(button => button.text() === '采集评论')!.trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({ path: '/acquisition', query: { create: '1', platform: 'dy', creator: 'https://example.com/competitor' } })
    await wrapper.setProps({ workbench: { items: [{ id: 7, target_type: 'lead', result_label: '目标客户' }] }, query: { tab: 'leads', keyword: '', page: 1, page_size: 10 } })
    await wrapper.findAll('button').find(button => button.text() === '跟进')!.trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({ path: '/message-workbench', query: { customer: '7', status: '全部' } })
    expect(wrapper.emitted('create-job')).toBeUndefined()
    wrapper.unmount()
  })
  it.each(['关键词A,关键词B', '关键词A，关键词B'])('keeps every search term when continuing to follow-up: %s', async keyword => {
    const wrapper = mount(AiPage, { props: { workbench: { items: [], total: 0 }, query: { tab: 'leads', keyword: '', status: '已分析', result: '目标客户', page: 2, page_size: 10 } } })
    await wrapper.get('.ai-search input').setValue(keyword)
    await wrapper.findAll('button').find(button => button.text() === '前往客户跟进')!.trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({ path: '/message-workbench', query: { query: keyword, status: '待私信' } })
    expect(wrapper.emitted('query-change')).toBeUndefined()
    wrapper.unmount()
  })
  it('loads the exact job outside the current list and refreshes that detail without clearing it', async () => {
    mocks.route.query = { job: 'historical-job' }
    let status = 'pending'
    mocks.get.mockImplementation(async () => ({ data: { id: 'historical-job', target_id: 77, target_type: 'lead', target_name: '历史客户', status, input_payload: { comments: ['当时的评论证据'] }, output_payload: { reason: '意向原因', script: '跟进话术' } } }))
    const wrapper = mount(AiPage, { props: { workbench: { items: [], total: 0 } } })
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledWith('/ai/jobs/historical-job')
    expect(wrapper.text()).toContain('历史客户')
    expect(wrapper.text()).toContain('当时的评论证据')
    expect(wrapper.text()).toContain('跟进话术')
    status = 'succeeded'
    await wrapper.setProps({ workbench: { items: [{ id: 1 }], total: 1 } })
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('historical-job')
    expect(wrapper.text()).toContain('succeeded')
    wrapper.unmount()
  })
})
