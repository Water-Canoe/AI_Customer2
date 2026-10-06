import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import WorkspacePage from './WorkspacePage'

const mocks = vi.hoisted(() => ({ get: vi.fn(), push: vi.fn() }))
vi.mock('../shared/api', () => ({ api: { get: mocks.get } }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: mocks.push }) }))
vi.mock('element-plus', () => ({ ElMessage: {}, ElMessageBox: {}, ElPagination: { render: () => null } }))

function response(url: string, jobName = '本次视频') {
  if (url === '/accounts') return { data: [{ enabled: true, features: ['acquisition'], feature_status: { acquisition: { status: 'ready' } } }] }
  if (url === '/runtime/jobs') return { data: { items: [{ id: 'job', kind: 'video_generation', status: 'queued', display_name: jobName, detail_url: '/content-records?job=job' }] } }
  return { data: { metrics: { ai_pending: 3, pending_customers: 7, unpublished_outputs: 1, failed: 2, interrupted: 1 } } }
}

describe('工作台的业务入口与读取状态', () => {
  afterEach(() => { vi.clearAllMocks() })

  it('显示完整摘要和业务任务名称，读取失败保留上次结果', async () => {
    mocks.get.mockImplementation(async (url: string) => response(url))
    const wrapper = mount(WorkspacePage)
    await flushPromises()
    expect(wrapper.findAll('.workspace-metric strong').map(node => node.text())).toEqual(['3', '7', '1', '3'])
    expect(wrapper.find('.workspace-onboarding').exists()).toBe(false)
    await wrapper.findAll('.workspace-metric')[2].trigger('click')
    expect(mocks.push).toHaveBeenCalledWith('/content-records?tab=outputs')
    await wrapper.get('.workspace-recent-row').trigger('click')
    expect(mocks.push).toHaveBeenCalledWith('/content-records?job=job')
    mocks.get.mockRejectedValue(new Error('读取失败'))
    await wrapper.setProps({ refreshSeq: 1 })
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('已有数据已保留')
    expect(wrapper.get('.workspace-recent-row').text()).toContain('本次视频')
    wrapper.unmount()
  })

  it('较慢的旧刷新不会覆盖最新任务', async () => {
    let finish!: (value: any) => void
    mocks.get.mockImplementation((url: string) => url === '/runtime/jobs'
      ? new Promise(resolve => { finish = resolve }) : Promise.resolve(response(url)))
    const wrapper = mount(WorkspacePage)
    await flushPromises()
    mocks.get.mockImplementation(async (url: string) => response(url, '最新任务'))
    await wrapper.setProps({ refreshSeq: 1 })
    await flushPromises()
    finish(response('/runtime/jobs', '过期任务'))
    await flushPromises()
    expect(wrapper.get('.workspace-recent-row').text()).toContain('最新任务')
    expect(wrapper.text()).not.toContain('过期任务')
    wrapper.unmount()
  })
})
