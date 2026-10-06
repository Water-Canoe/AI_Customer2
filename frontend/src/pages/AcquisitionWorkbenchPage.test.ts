import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AcquisitionWorkbenchPage from './AcquisitionWorkbenchPage'
import { TagInput } from '../components/ui/TagInput'

const mocks = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), remove: vi.fn(), push: vi.fn(), replace: vi.fn(), route: { query: { create: '1' } as Record<string, string>, fullPath: '/acquisition?create=1' } }))
vi.mock('../shared/api', () => ({ api: { get: mocks.get, put: mocks.put, delete: mocks.remove } }))
vi.mock('vue-router', () => ({ useRoute: () => mocks.route, useRouter: () => ({ push: mocks.push, replace: mocks.replace }), onBeforeRouteLeave: () => undefined }))
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  const modal = defineComponent({ props: { modelValue: Boolean }, setup: (props, { slots }) => () => props.modelValue ? h('div', slots.default?.()) : null })
  return { ElDialog: modal, ElDrawer: modal, ElPagination: { render: () => null }, ElMessage: { error: vi.fn(), warning: vi.fn() } }
})

describe('采集创建草稿', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.query = { create: '1' }
    mocks.get.mockImplementation(async (url: string) => ({ data: url === '/drafts/crawl/new'
      ? { exists: true, payload: { mode: 'competitor_discovery', keyword_tags: ['原关键词'], account_id: 'account-1' } }
      : [{ id: 'account-1', name: '执行账号', platform: 'dy', enabled: true, features: ['acquisition'], feature_status: { acquisition: { status: 'ready' } } }] }))
    mocks.put.mockResolvedValue({ data: { updated_at: 'now' } })
    mocks.remove.mockResolvedValue({ data: {} })
  })
  it('restores and saves the draft before going to accounts, then clears only after successful creation', async () => {
    const wrapper = mount(AcquisitionWorkbenchPage, { props: { tasks: [], settings: {}, onCreateTask: async (_payload: object, created: (task: { id: string }) => Promise<void>, settled: () => void) => { await created({ id: 'task-1' }); settled() } } })
    await flushPromises()
    const tags = wrapper.findAllComponents(TagInput)[0]
    expect(tags.props('modelValue')).toEqual(['原关键词'])
    tags.vm.$emit('update:modelValue', ['当前关键词'])
    await wrapper.vm.$nextTick()
    await wrapper.findAll('button').find(button => button.text() === '配置执行账号')!.trigger('click')
    await flushPromises()
    expect(mocks.put).toHaveBeenCalledWith('/drafts/crawl/new', { payload: expect.objectContaining({ keyword_tags: ['当前关键词'] }) })
    expect(mocks.push).toHaveBeenCalledWith({ path: '/accounts', query: { feature: 'acquisition', platform: 'dy', return_to: '/acquisition?create=1' } })
    expect(mocks.remove).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(button => button.text() === '开始采集并导入')!.trigger('click')
    await flushPromises()
    expect(wrapper.emitted('create-task')?.[0][0]).toMatchObject({ keywords: '当前关键词', account_id: 'account-1' })
    expect(mocks.remove).toHaveBeenCalledWith('/drafts/crawl/new')
    wrapper.unmount()
  })
  it('prefills the selected competitor for comment collection and consumes the navigation parameters', async () => {
    mocks.route.query = { create: '1', platform: 'dy', creator: 'https://example.com/competitor' }
    const wrapper = mount(AcquisitionWorkbenchPage, { props: { tasks: [], settings: {} } })
    await flushPromises()
    expect(wrapper.findAllComponents(TagInput)[0].props('modelValue')).toEqual(['https://example.com/competitor'])
    expect(mocks.replace).toHaveBeenCalledWith({ path: '/acquisition', query: { create: '1' } })
    await wrapper.findAll('button').find(button => button.text() === '开始采集并导入')!.trigger('click')
    expect(wrapper.emitted('create-task')?.[0][0]).toMatchObject({ mode: 'competitor_crawl', creator_id: 'https://example.com/competitor', keywords: '', collect_comments: true })
    wrapper.unmount()
  })
  it('recovers a failed read without losing edits and retries cleanup without creating a second task', async () => {
    mocks.get.mockRejectedValueOnce(new Error('读取失败'))
    mocks.remove.mockRejectedValueOnce(new Error('清理失败'))
    const create = vi.fn(async (_payload: object, created: (task: { id: string }) => Promise<void>, settled: () => void) => { await created({ id: 'created-task' }); settled() })
    const wrapper = mount(AcquisitionWorkbenchPage, { props: { tasks: [], settings: {}, onCreateTask: create } })
    await flushPromises()
    expect(wrapper.text()).toContain('重新读取草稿')
    wrapper.findAllComponents(TagInput)[0].vm.$emit('update:modelValue', ['读取失败期间的编辑'])
    await wrapper.vm.$nextTick()
    await wrapper.findAll('button').find(button => button.text() === '保存草稿')!.trigger('click')
    await flushPromises()
    expect(mocks.put).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(button => button.text() === '重新读取草稿')!.trigger('click')
    await flushPromises()
    expect(wrapper.findAllComponents(TagInput)[0].props('modelValue')).toEqual(['读取失败期间的编辑'])
    await wrapper.findAll('button').find(button => button.text() === '开始采集并导入')!.trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('任务已创建，草稿清理失败')
    expect(wrapper.findAll('button').find(button => button.text() === '新建采集')!.attributes('disabled')).toBeDefined()
    expect(mocks.put).toHaveBeenCalledWith('/drafts/crawl/new', { payload: expect.objectContaining({ return_context: expect.objectContaining({ query: { create: '1', task: 'created-task' } }) }) })
    await wrapper.findAll('button').find(button => button.text() === '重试清理草稿')!.trigger('click')
    await flushPromises()
    expect(mocks.remove).toHaveBeenCalledTimes(2)
    expect(create).toHaveBeenCalledOnce()
    expect(wrapper.text()).not.toContain('草稿清理失败')
    wrapper.unmount()
  })
  it('honors a submitted server draft after a failed read even when local edits are preserved', async () => {
    mocks.get.mockRejectedValueOnce(new Error('读取失败'))
    const get = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation(async (url: string) => url === '/drafts/crawl/new' ? { data: { exists: true, submitted: true, payload: { return_context: { query: { task: 'already-created' } }, keyword_tags: ['服务端关键词'] } } } : get(url))
    const wrapper = mount(AcquisitionWorkbenchPage, { props: { tasks: [], settings: {} } })
    await flushPromises()
    wrapper.findAllComponents(TagInput)[0].vm.$emit('update:modelValue', ['本地输入'])
    await wrapper.vm.$nextTick()
    await wrapper.findAll('button').find(button => button.text() === '重新读取草稿')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('任务已创建')
    expect(wrapper.text()).toContain('already-created')
    expect(wrapper.findAll('button').find(button => button.text() === '任务已创建')!.attributes('disabled')).toBeDefined()
    expect(wrapper.emitted('create-task')).toBeUndefined()
    wrapper.unmount()
  })
})
