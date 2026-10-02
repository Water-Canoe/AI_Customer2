import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ListPagination } from '../components/ui/ListPagination'
import TrafficWorkbenchPage from './TrafficWorkbenchPage'

const mocks = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('../shared/api', () => ({ api: { get: mocks.get } }))
vi.mock('vue-router', () => ({ useRoute: () => ({ name: 'traffic-monitor', query: {} }) }))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
  ElPagination: { render: () => null },
}))

describe('引流监控刷新', () => {
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
    expect(mocks.get.mock.calls.filter(([url]) => url === '/traffic/runs/first')).toHaveLength(1)

    const pagination = wrapper.findAllComponents(ListPagination)[0]
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
})
