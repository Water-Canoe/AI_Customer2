import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import { useOverviewWorkbench } from './overviewWorkbench'


const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  remove: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  refreshTasks: vi.fn(),
  refreshAi: vi.fn(),
  refreshTable: vi.fn(),
  openTaskLogs: vi.fn(),
}))

vi.mock('../shared/api', () => ({
  api: { get: mocks.get, post: mocks.post, patch: mocks.patch, delete: mocks.remove },
}))
vi.mock('../shared/bulkPreview', () => ({ confirmBulkPreview: vi.fn() }))
vi.mock('element-plus', () => ({
  ElMessage: { success: mocks.success, error: mocks.error, info: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))


describe('useOverviewWorkbench', () => {
  beforeEach(() => vi.clearAllMocks())

  it('creates an account analysis task and opens its logs', async () => {
    mocks.post.mockResolvedValue({ data: { id: 'task-1' } })
    mocks.get.mockResolvedValue({ data: [{ id: 'dy' }] })
    const overview = useOverviewWorkbench({
      settings: ref({}),
      refreshTasks: mocks.refreshTasks,
      refreshAi: mocks.refreshAi,
      refreshTable: mocks.refreshTable,
      openTaskLogs: mocks.openTaskLogs,
    })

    await overview.analyzeAccount({ metrics: { id: 9 } })

    expect(mocks.post).toHaveBeenCalledWith('/accounts/9/analysis')
    expect(overview.tree.value).toEqual([{ id: 'dy' }])
    expect(mocks.refreshTasks).toHaveBeenCalledOnce()
    expect(mocks.refreshAi).toHaveBeenCalledOnce()
    expect(mocks.openTaskLogs).toHaveBeenCalledWith('task-1')
  })
})
