import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LogsPage from './LogsPage'
import { ListPagination, type PageChange } from '../components/ui/ListPagination'

const mocks = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), route: { query: { task: 'task-1' } } }))
vi.mock('vue-router', () => ({ useRoute: () => mocks.route, useRouter: () => ({ push: mocks.push, replace: mocks.replace }) }))
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    ElDrawer: defineComponent({ props: { modelValue: Boolean }, setup: (props, { slots }) => () => props.modelValue ? h('div', slots.default?.()) : null }),
    ElPagination: { render: () => null },
  }
})

describe('采集结果与原始日志', () => {
  beforeEach(() => vi.clearAllMocks())

  function task() {
    return { id: 'task-1', name: '采集任务', platform: 'dy', status: 'succeeded', keywords: '工业机器人,机器视觉', outcome: { counts: { competitor_candidates: 2, leads: 2 } }, logs: Array.from({ length: 55 }, (_, index) => ({ id: index + 1, level: 'info', created_at: '2026-10-06 12:00:00', message: `line-${index + 1}` })) }
  }

  it('keeps both collection keywords when continuing to customer analysis', async () => {
    const wrapper = mount(LogsPage, { props: { tasks: [], selectedTask: task() } })
    await wrapper.findAll('button').find(button => button.text() === '分析客户意向')!.trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({ path: '/ai', query: { tab: 'leads', keyword: '工业机器人,机器视觉' } })
    wrapper.unmount()
  })

  it('renders logs only when expanded, exposes later pages, and resets for another task', async () => {
    const wrapper = mount(LogsPage, { props: { tasks: [], selectedTask: task() } })
    expect(wrapper.findAll('.traffic-log-item')).toHaveLength(0)
    const details = wrapper.get('.task-raw-logs')
    const detailsElement = details.element as HTMLDetailsElement
    detailsElement.open = true
    await details.trigger('toggle')
    await flushPromises()
    expect(wrapper.findAll('.traffic-log-item')).toHaveLength(50)
    expect(details.text()).not.toContain('line-55')
    details.getComponent(ListPagination).vm.$emit('change', { page: 2, page_size: 50 } satisfies PageChange)
    await flushPromises()
    expect(wrapper.findAll('.traffic-log-item')).toHaveLength(5)
    expect(details.text()).toContain('line-55')
    await wrapper.setProps({ selectedTask: { ...task(), id: 'task-2', logs: [{ id: 56, message: '另一任务日志' }] } })
    expect(wrapper.findAll('.traffic-log-item')).toHaveLength(0)
    expect((wrapper.get('.task-raw-logs').element as HTMLDetailsElement).open).toBe(false)
    wrapper.unmount()
  })
})
