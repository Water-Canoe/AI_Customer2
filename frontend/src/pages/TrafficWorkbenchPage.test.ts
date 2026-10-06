import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ListPagination } from '../components/ui/ListPagination'
import TrafficWorkbenchPage from './TrafficWorkbenchPage'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), remove: vi.fn(), push: vi.fn(), replace: vi.fn(), route: { name: 'traffic-monitor', query: {} as Record<string, string> } }))

vi.mock('../shared/api', () => ({ api: { get: mocks.get, post: mocks.post, patch: mocks.patch, put: mocks.put, delete: mocks.remove } }))
vi.mock('vue-router', async () => {
  const { reactive } = await import('vue')
  mocks.route = reactive(mocks.route)
  return { useRoute: () => mocks.route, useRouter: () => ({ push: mocks.push, replace: mocks.replace }), onBeforeRouteLeave: () => undefined, onBeforeRouteUpdate: () => undefined }
})
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  const modal = defineComponent({ props: { modelValue: Boolean }, setup: (props, { slots }) => () => props.modelValue ? h('div', { class: 'test-modal' }, slots.default?.()) : null })
  return { ElDrawer: modal, ElDialog: modal, ElMessage: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }, ElMessageBox: { confirm: vi.fn() }, ElPagination: { render: () => null } }
})

describe('引流监控刷新', () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.route.name = 'traffic-monitor'; mocks.route.query = {}; mocks.put.mockResolvedValue({ data: { updated_at: 'now' } }); mocks.remove.mockResolvedValue({ data: {} }) })
  afterEach(() => vi.clearAllMocks())

  it('loads each selected run once and preserves its detail page until switching runs', async () => {
    const runs = [{ id: 'first', status: 'completed' }, { id: 'second', status: 'completed' }]
    const records = Array.from({ length: 12 }, (_, index) => ({ video_desc: `视频${index + 1}` }))
    mocks.get.mockImplementation(async (url: string) => ({
      data: url === '/traffic/runs'
        ? runs
        : { ...runs.find(run => url.endsWith(`/${run.id}`)), records, logs: [] },
    }))
    const wrapper = mount(TrafficWorkbenchPage)
    await flushPromises()
    expect(mocks.get.mock.calls.filter(([url]) => url === '/traffic/runs/first')).toHaveLength(0)
    await wrapper.findAll('.traffic-side-table tbody tr')[0].trigger('click')
    await flushPromises()
    expect(mocks.get.mock.calls.filter(([url]) => url === '/traffic/runs/first')).toHaveLength(1)

    const pagination = wrapper.findAllComponents(ListPagination).find(component => component.props('total') === 12)!
    pagination.vm.$emit('change', { page: 2, page_size: 10 })
    await wrapper.vm.$nextTick()
    expect(pagination.props('page')).toBe(2)
    expect(wrapper.get('.traffic-detail-table').text()).toContain('视频11')

    await wrapper.setProps({ refreshSeq: 1 })
    await flushPromises()
    expect(mocks.get.mock.calls.filter(([url]) => url === '/traffic/runs/first')).toHaveLength(2)
    expect(pagination.props('page')).toBe(2)

    await wrapper.findAll('.traffic-side-table tbody tr')[1].trigger('click')
    await flushPromises()
    expect(pagination.props('page')).toBe(1)
    expect(mocks.get.mock.calls.filter(([url]) => url === '/traffic/runs/second')).toHaveLength(1)
    wrapper.unmount()
  })
  it('edits the existing plan with PATCH and copies it through a new POST', async () => {
    mocks.route.name = 'traffic-plans'
    const plan = { id: 'plan-1', name: '已有计划', platform: 'dy', source_mode: 'random_feed', account_id: 'account-1', round_video_limit: 5 }
    mocks.get.mockImplementation(async (url: string) => ({ data: url === '/traffic/plans' ? [plan] : url === '/accounts' ? [{ id: 'account-1', name: '执行账号', platform: 'dy', enabled: true, features: ['traffic'], feature_status: { traffic: { status: 'ready' } } }] : url.startsWith('/drafts/') ? { exists: false, payload: {} } : [] }))
    mocks.patch.mockResolvedValue({ data: plan })
    mocks.post.mockResolvedValue({ data: { ...plan, id: 'plan-2' } })
    const wrapper = mount(TrafficWorkbenchPage)
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '编辑')!.trigger('click')
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledWith('/drafts/traffic/plan-1')
    await wrapper.findAll('button').find(button => button.text() === '保存计划')!.trigger('click')
    await flushPromises()
    expect(mocks.patch).toHaveBeenCalledWith('/traffic/plans/plan-1', expect.objectContaining({ name: '已有计划', account_id: 'account-1' }))
    expect(mocks.remove).toHaveBeenCalledWith('/drafts/traffic/plan-1')
    await wrapper.findAll('button').find(button => button.text() === '复制')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '保存计划')!.trigger('click')
    await flushPromises()
    expect(mocks.post).toHaveBeenCalledWith('/traffic/plans', expect.objectContaining({ name: '已有计划 副本', account_id: 'account-1' }))
    expect(mocks.post.mock.calls[0][1]).not.toHaveProperty('id')
    wrapper.unmount()
  })
  it('opens the requested run even when it is outside the current list filter', async () => {
    mocks.route.query = { run: 'historical-run', plan: 'plan-1' }
    mocks.get.mockImplementation(async (url: string) => ({ data: url === '/traffic/runs' ? [] : { id: 'historical-run', status: 'completed', plan_name: '历史计划', logs: [], records: [] } }))
    const wrapper = mount(TrafficWorkbenchPage)
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledWith('/traffic/runs', { params: { include_archived: false, plan_id: 'plan-1' } })
    expect(mocks.get.mock.calls.filter(([url]) => url === '/traffic/runs/historical-run')).toHaveLength(1)
    expect(wrapper.text()).toContain('已完成 · 批次 historical-run')
    wrapper.unmount()
  })
  it('keeps a failed draft read accessible and retries cleanup without another plan POST', async () => {
    mocks.route.name = 'traffic-plans'
    mocks.route.query = { create: '1' }
    let reads = 0
    mocks.get.mockImplementation(async (url: string) => {
      if (url === '/drafts/traffic/new') {
        if (!reads++) throw new Error('读取失败')
        return { data: { exists: false, payload: {} } }
      }
      return { data: url === '/accounts' ? [{ id: 'account-1', name: '执行账号', platform: 'dy', enabled: true, features: ['traffic'], feature_status: { traffic: { status: 'ready' } } }] : [] }
    })
    mocks.post.mockResolvedValue({ data: { id: 'saved-plan' } })
    mocks.remove.mockRejectedValueOnce(new Error('清理失败'))
    const wrapper = mount(TrafficWorkbenchPage)
    await flushPromises()
    expect(wrapper.text()).toContain('重新读取草稿')
    await wrapper.findAll('button').find(button => button.text() === '重新读取草稿')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '保存计划')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('计划已保存，草稿清理失败')
    expect(wrapper.findAll('button').find(button => button.text() === '保存计划')!.attributes('disabled')).toBeDefined()
    expect(mocks.put).toHaveBeenCalledWith('/drafts/traffic/new', { payload: expect.objectContaining({ return_context: expect.objectContaining({ query: { create: '1', edit: 'saved-plan' } }) }) })
    await wrapper.findAll('button').find(button => button.text() === '重试清理草稿')!.trigger('click')
    await flushPromises()
    expect(mocks.remove).toHaveBeenCalledTimes(2)
    expect(mocks.post).toHaveBeenCalledOnce()
    wrapper.unmount()
  })
  it('discards settings only after leaving and ignores an upload completing in the previous settings visit', async () => {
    mocks.route.name = 'traffic-settings'
    let limit = 100
    mocks.get.mockImplementation(async (url: string) => ({ data: url === '/traffic/settings' ? { values: { traffic_daily_action_limit: limit }, texts: [], images: [] } : url === '/traffic/environment-check' ? {} : [] }))
    let uploaded: (value: unknown) => void = () => undefined
    mocks.post.mockImplementation(() => new Promise(resolve => uploaded = resolve))
    const wrapper = mount(TrafficWorkbenchPage)
    await flushPromises()
    await wrapper.get('.traffic-settings-form input').setValue('999')
    const fileInput = wrapper.get('.traffic-image-input')
    Object.defineProperty(fileInput.element, 'files', { value: [new File(['image'], 'image.png', { type: 'image/png' })] })
    await fileInput.trigger('change')
    mocks.route.name = 'traffic-plans'
    await flushPromises()
    uploaded({ data: { path: 'image.png', preview_url: '/image.png' } })
    await flushPromises()
    limit = 200
    mocks.route.name = 'traffic-settings'
    await flushPromises()
    expect((wrapper.get('.traffic-settings-form input').element as HTMLInputElement).value).toBe('200')
    expect(wrapper.text()).not.toContain('有未保存修改')
    expect(wrapper.findAll('img').some(image => image.attributes('src') === '/image.png')).toBe(false)
    wrapper.unmount()
  })
  it('restores record filters and pages on reentry while keeping the other traffic views independent', async () => {
    mocks.route.name = 'traffic-records'
    mocks.route.query = { query: '评论证据', status: 'failed', platform: 'dy', action: 'like', page: '2', page_size: '10' }
    mocks.get.mockImplementation(async (url: string) => ({ data: url === '/traffic/records' ? { rows: [], total: 35, page: 2, page_size: 10 } : [] }))
    let wrapper = mount(TrafficWorkbenchPage)
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledWith('/traffic/records', { params: { query: '评论证据', status: 'failed', platform: 'dy', action: 'like', page: 2, page_size: 10 } })
    wrapper.getComponent(ListPagination).vm.$emit('change', { page: 3, page_size: 10 })
    await flushPromises()
    const query = mocks.replace.mock.calls[mocks.replace.mock.calls.length - 1][0].query
    expect(query).toMatchObject({ status: 'failed', query: '评论证据', page: 3 })
    wrapper.unmount()
    mocks.route.query = query
    wrapper = mount(TrafficWorkbenchPage)
    await flushPromises()
    expect((wrapper.get('input[placeholder="搜索视频/作者/评论"]').element as HTMLInputElement).value).toBe('评论证据')
    expect(mocks.get).toHaveBeenLastCalledWith('/traffic/records', { params: expect.objectContaining({ status: 'failed', page: 3 }) })
    await wrapper.findAll('nav[aria-label="引流工作区"] button').find(button => button.text() === '计划')!.trigger('click')
    expect(wrapper.emitted('navigate-view')).toEqual([['traffic-plans']])
    expect(mocks.push).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
