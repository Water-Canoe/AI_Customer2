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
  it('previews the full unanalysed scope and lets the server choose targets inside its transaction', async () => {
    mocks.preview.mockResolvedValue({ eligible_count: 25 })
    mocks.post.mockResolvedValue({ data: Array.from({ length: 25 }, (_, id) => ({ id })) })
    mocks.get.mockResolvedValue({ data: { items: [], total_pages: 1 } })
    const ai = useAiWorkbench({ refreshRelated: mocks.refreshRelated })
    ai.query.value = { tab: 'leads', keyword: '当前筛选', status: '已分析', result: '非客户', page: 3, page_size: 10 }
    await ai.createBatchJobs('lead')
    expect(mocks.preview).toHaveBeenCalledWith({ action: 'ai_analyze', target_type: 'lead', filters: { unanalysed_only: true } }, '一键分析预览')
    expect(mocks.post).toHaveBeenCalledWith('/ai/jobs/batch', { target_type: 'lead' })
    expect(mocks.success).toHaveBeenCalledWith('已将 25 个AI分析任务加入队列')
  })
  it('does not enqueue anything or report an error after cancelling the full-scope preview', async () => {
    mocks.preview.mockRejectedValueOnce('cancel')
    mocks.get.mockResolvedValue({ data: { items: [], total_pages: 1 } })
    await useAiWorkbench({ refreshRelated: mocks.refreshRelated }).createBatchJobs('competitor')
    expect(mocks.post).not.toHaveBeenCalled()
    expect(mocks.error).not.toHaveBeenCalled()
  })

  it('creates an AI job and refreshes its related views', async () => {
    mocks.post.mockResolvedValue({ data: {} })
    mocks.get.mockResolvedValue({ data: { items: [{ id: 'job-1' }], total_pages: 1 } })
    const ai = useAiWorkbench({ refreshRelated: mocks.refreshRelated })

    await ai.createJob('lead', 7)

    expect(mocks.post).toHaveBeenCalledWith('/ai/jobs', { target_type: 'lead', target_id: 7 })
    expect(ai.workbench.value.items).toEqual([{ id: 'job-1' }])
    expect(mocks.refreshRelated).toHaveBeenCalledOnce()
    expect(mocks.success).toHaveBeenCalledWith('AI分析已加入队列')
  })
  it('ignores an old query response after a newer filter has completed', async () => {
    let first: (value: any) => void = () => undefined
    mocks.get.mockImplementationOnce(() => new Promise(resolve => first = resolve))
    mocks.get.mockResolvedValueOnce({ data: { tab: 'leads', items: [{ id: 7 }], total_pages: 1 } })
    const ai = useAiWorkbench({ refreshRelated: mocks.refreshRelated })
    const pending = ai.changeQuery({ tab: 'competitors', keyword: '旧条件' })
    await ai.changeQuery({ tab: 'leads', keyword: '当前条件' })
    first({ data: { tab: 'competitors', items: [{ id: 1 }], total_pages: 1 } })
    await pending
    expect(ai.query.value).toMatchObject({ tab: 'leads', keyword: '当前条件' })
    expect(ai.workbench.value.items).toEqual([{ id: 7 }])
  })
})
