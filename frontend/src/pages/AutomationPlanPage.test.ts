import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AutomationPlanPage from './AutomationPlanPage'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), remove: vi.fn(), push: vi.fn(), replace: vi.fn(), route: { query: {} as Record<string, string> } }))
vi.mock('../shared/api', () => ({ api: { get: mocks.get, post: mocks.post, put: mocks.put, delete: mocks.remove } }))
vi.mock('vue-router', () => ({ useRoute: () => mocks.route, useRouter: () => ({ push: mocks.push, replace: mocks.replace }), onBeforeRouteLeave: () => undefined }))
vi.mock('element-plus', async importOriginal => {
  const actual = await importOriginal<typeof import('element-plus')>()
  const { defineComponent, h } = await import('vue')
  const modal = defineComponent({ props: { modelValue: Boolean }, setup: (props, { slots }) => () => props.modelValue ? h('div', [slots.default?.(), slots.footer?.()]) : null })
  return { ...actual, ElDialog: modal, ElDrawer: modal, ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }
})

describe('自动化详情与草稿', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.query = {}
    mocks.put.mockResolvedValue({ data: { updated_at: 'now' } })
    mocks.remove.mockResolvedValue({ data: {} })
  })
  it('restores the message plan draft, then clears it after saving without starting execution', async () => {
    mocks.route.query = { create: '1', type: 'message' }
    const draft = { id: '', name: '未提交的私信计划', plan_type: 'message', weekdays: [1, 3], run_time: '10:30', enabled: false, config: { platform: 'dy', account_id: 'account-1', keyword_scope: 'all', keywords: [], count: 12, script_mode: 'fixed', fixed_script: '问候语', interval_min_seconds: 30, interval_max_seconds: 60 } }
    mocks.get.mockImplementation(async (url: string) => ({ data: url === '/automation/plans' ? { items: [], summary: {} } : url === '/automation/runs' ? { items: [], page: 1, page_size: 10, total: 0 } : url === '/accounts' ? [{ id: 'account-1', name: '私信账号', platform: 'dy', enabled: true, features: ['message'], feature_status: { message: { status: 'ready' } } }] : { exists: true, payload: draft } }))
    mocks.post.mockResolvedValue({ data: { id: 'plan-1' } })
    const wrapper = mount(AutomationPlanPage)
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledWith('/drafts/automation/new-message')
    expect(wrapper.findAll('input').some(input => (input.element as HTMLInputElement).value === '未提交的私信计划')).toBe(true)
    await wrapper.findAll('button').find(button => button.text() === '保存计划')!.trigger('click')
    await flushPromises()
    expect(mocks.post).toHaveBeenCalledWith('/automation/plans', expect.objectContaining({ name: draft.name, enabled: false, config: expect.objectContaining({ count: 12, fixed_script: '问候语' }) }))
    expect(mocks.remove).toHaveBeenCalledWith('/drafts/automation/new-message')
    expect(mocks.post.mock.calls.some(([url]) => String(url).endsWith('/run'))).toBe(false)
    wrapper.unmount()
  })
  it('opens the referenced run and refreshes its current stage', async () => {
    mocks.route.query = { run: 'run-1', plan: 'plan-1' }
    let stage = 'queued'
    mocks.get.mockImplementation(async (url: string) => ({ data: url === '/automation/plans' ? { items: [], summary: {} } : url === '/automation/runs' ? { items: [], page: 1, page_size: 10, total: 0 } : url === '/accounts' ? [] : { id: 'run-1', plan_name: '执行中的计划', plan_type: 'message', status: stage, current_stage: stage, items: [] } }))
    const wrapper = mount(AutomationPlanPage)
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledWith('/automation/runs', { params: expect.objectContaining({ plan_id: 'plan-1' }) })
    expect(wrapper.text()).toContain('排队中')
    stage = 'running'
    await wrapper.setProps({ refreshSeq: 1 })
    await flushPromises()
    expect(wrapper.text()).toContain('运行中')
    expect(mocks.get.mock.calls.filter(([url]) => url === '/automation/runs/run-1')).toHaveLength(2)
    wrapper.unmount()
  })
  it('keeps a failed draft read accessible and retries cleanup without creating another automation plan', async () => {
    mocks.route.query = { create: '1', type: 'message' }
    let reads = 0
    mocks.get.mockImplementation(async (url: string) => {
      if (url === '/drafts/automation/new-message') {
        if (!reads++) throw new Error('读取失败')
        return { data: { exists: true, payload: { id: '', name: '计划', plan_type: 'message', weekdays: [1], run_time: '10:00', enabled: false, config: { platform: 'dy', account_id: 'account-1', count: 1 } } } }
      }
      return { data: url === '/automation/plans' ? { items: [], summary: {} } : url === '/automation/runs' ? { items: [], total: 0 } : [{ id: 'account-1', name: '执行账号', platform: 'dy', enabled: true, features: ['message'], feature_status: { message: { status: 'ready' } } }] }
    })
    mocks.post.mockResolvedValue({ data: { id: 'saved-plan' } })
    mocks.remove.mockRejectedValueOnce(new Error('清理失败'))
    const wrapper = mount(AutomationPlanPage)
    await flushPromises()
    expect(wrapper.text()).toContain('重新读取草稿')
    await wrapper.findAll('button').find(button => button.text() === '重新读取草稿')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '保存计划')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('计划已保存，草稿清理失败')
    expect(wrapper.findAll('button').find(button => button.text() === '保存计划')!.attributes('disabled')).toBeDefined()
    expect(mocks.put).toHaveBeenCalledWith('/drafts/automation/new-message', { payload: expect.objectContaining({ id: 'saved-plan' }) })
    await wrapper.findAll('button').find(button => button.text() === '重试清理草稿')!.trigger('click')
    await flushPromises()
    expect(mocks.remove).toHaveBeenCalledTimes(2)
    expect(mocks.post).toHaveBeenCalledOnce()
    wrapper.unmount()
  })
})
