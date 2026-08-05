import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useTableWorkbench } from './tableWorkbench'


const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  remove: vi.fn(),
  post: vi.fn(),
}))

vi.mock('../shared/api', () => ({
  api: { get: mocks.get, patch: mocks.patch, delete: mocks.remove, post: mocks.post },
}))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))


describe('useTableWorkbench', () => {
  beforeEach(() => vi.clearAllMocks())

  it('returns to the last existing page after the current page becomes empty', async () => {
    mocks.get
      .mockResolvedValueOnce({ data: { rows: [], total: 20, page: 3, page_size: 10, total_pages: 2 } })
      .mockResolvedValueOnce({ data: { rows: [{ id: 1 }], total: 20, page: 2, page_size: 10, total_pages: 2 } })
    const table = useTableWorkbench({
      analyzeJob: vi.fn(),
      refreshTasks: vi.fn(),
      refreshOverview: vi.fn(),
      openTaskLogs: vi.fn(),
    })
    table.page.value = 3
    table.pageSize.value = 10

    await table.load()

    expect(table.page.value).toBe(2)
    expect(table.rows.value).toEqual([{ id: 1 }])
    expect(mocks.get).toHaveBeenCalledTimes(2)
  })
})
