import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAiWorkbench } from './aiWorkbench'


const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  preview: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
  refreshRelated: vi.fn(),
}))

vi.mock('../shared/api', () => ({ api: { get: mocks.get, post: mocks.post } }))
vi.mock('../shared/bulkPreview', () => ({ confirmBulkPreview: mocks.preview }))
vi.mock('element-plus', () => ({
  ElMessage: { success: mocks.success, error: mocks.error, info: mocks.info, warning: mocks.warning },
}))


describe('useAiWorkbench', () => {
  beforeEach(() => vi.clearAllMocks())

  it('creates an AI job and refreshes its related views', async () => {
    mocks.post.mockResolvedValue({ data: {} })
    mocks.get.mockResolvedValue({ data: { items: [{ id: 'job-1' }], total_pages: 1 } })
    const ai = useAiWorkbench({ refreshRelated: mocks.refreshRelated })

    await ai.createJob('lead', 7)

    expect(mocks.post).toHaveBeenCalledWith('/ai/jobs', { target_type: 'lead', target_id: 7, run_now: true })
    expect(ai.workbench.value.items).toEqual([{ id: 'job-1' }])
    expect(mocks.refreshRelated).toHaveBeenCalledOnce()
    expect(mocks.success).toHaveBeenCalledWith('AI分析完成')
  })
})
