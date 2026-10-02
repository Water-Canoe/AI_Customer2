import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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
  writeText: vi.fn(),
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
  afterEach(() => vi.unstubAllGlobals())

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

  it.each(['待筛选', '未私信', '已回复', '已移出'])('keeps customer messaging within the %s workflow', async (followStatus) => {
    mocks.get.mockResolvedValue({ data: [] })
    mocks.patch.mockResolvedValue({ data: {} })
    mocks.writeText.mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText: mocks.writeText } })
    vi.spyOn(window, 'open').mockReturnValue({ opener: window } as Window)
    const overview = useOverviewWorkbench({
      settings: ref({}), refreshTasks: mocks.refreshTasks, refreshAi: mocks.refreshAi,
      refreshTable: mocks.refreshTable, openTaskLogs: mocks.openTaskLogs,
    })
    await overview.messageCustomer({ metrics: {
      id: 7, script: '当前话术', profile_url: 'https://example.com/customer',
      follow_status: followStatus, screening_status: followStatus === '待筛选' ? '待筛选' : '目标客户',
    } })

    expect(mocks.writeText).toHaveBeenCalledWith('当前话术')
    if (['待筛选', '未私信'].includes(followStatus)) {
      expect(mocks.patch).toHaveBeenCalledWith('/overview/customers/7/follow-status', expect.objectContaining({
        follow_status: '已私信', record_message_attempt: true,
      }))
    } else {
      expect(mocks.patch).not.toHaveBeenCalled()
    }
  })

  it('restores a soft-removed customer and skips an unchanged follow status', async () => {
    mocks.get.mockResolvedValue({ data: [] })
    mocks.patch.mockResolvedValue({ data: {} })
    const overview = useOverviewWorkbench({
      settings: ref({}), refreshTasks: mocks.refreshTasks, refreshAi: mocks.refreshAi,
      refreshTable: mocks.refreshTable, openTaskLogs: mocks.openTaskLogs,
    })
    await overview.updateCustomerFollowStatus({ metrics: { id: 7, follow_status: '已移出', screening_status: '目标客户' } }, '未私信')
    await overview.updateCustomerFollowStatus({ metrics: { id: 7, follow_status: '未私信', screening_status: '目标客户' } }, '未私信')

    expect(mocks.patch).toHaveBeenCalledOnce()
    expect(mocks.patch).toHaveBeenCalledWith('/overview/customers/7/follow-status', {
      follow_status: '未私信', note: '人工修改跟进状态：已移出 -> 未私信',
    })
  })
})
