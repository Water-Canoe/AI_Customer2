import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useTaskWorkbench } from './taskWorkbench'


const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  remove: vi.fn(),
  push: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  refreshRelated: vi.fn(),
}))

vi.mock('../shared/api', () => ({
  api: { get: mocks.get, post: mocks.post, delete: mocks.remove },
}))

vi.mock('element-plus', () => ({
  ElMessage: { success: mocks.success, error: mocks.error },
  ElMessageBox: { confirm: vi.fn() },
}))


describe('useTaskWorkbench', () => {
  beforeEach(() => vi.clearAllMocks())

  it('creates a task, selects its detail and opens logs', async () => {
    const summary = { id: 'task-1', name: '测试任务' }
    const detail = { ...summary, logs: [] }
    mocks.post.mockResolvedValue({ data: summary })
    mocks.get.mockImplementation((url: string) => {
      if (url === '/tasks') return Promise.resolve({ data: { items: [summary], total: 1, page: 1, page_size: 10, total_pages: 1 } })
      if (url === '/tasks/task-1') return Promise.resolve({ data: detail })
      return Promise.resolve({ data: {} })
    })
    const workbench = useTaskWorkbench({ router: { push: mocks.push }, refreshRelated: mocks.refreshRelated })

    await workbench.createTask({ mode: 'competitor_discovery' })

    expect(workbench.tasks.value).toEqual([summary])
    expect(workbench.selected.value).toEqual(detail)
    expect(mocks.push).toHaveBeenCalledWith('/logs')
    expect(mocks.success).toHaveBeenCalledWith('任务 task-1 已创建')
  })
})
