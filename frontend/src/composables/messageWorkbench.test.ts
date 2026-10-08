import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import { selectDmScript, useMessageWorkbench } from './messageWorkbench'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), push: vi.fn(), patch: vi.fn(), writeText: vi.fn(), confirm: vi.fn() }))
vi.mock('../shared/api', () => ({ api: { get: mocks.get, post: mocks.post, patch: mocks.patch } }))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: mocks.confirm },
}))


describe('selectDmScript', () => {
  it('returns the configured fixed script when fixed mode is enabled', () => {
    const script = selectDmScript({ dm_script_mode: 'fixed', fixed_dm_script: '  固定问候语  ' }, 'AI问候语')

    expect(script).toMatchObject({ text: '固定问候语', label: '固定话术' })
  })

  it('returns the customer AI script in AI mode', () => {
    const script = selectDmScript({ dm_script_mode: 'ai' }, '  AI问候语  ')

    expect(script).toMatchObject({ text: 'AI问候语', label: 'AI话术' })
  })
})

describe('useMessageWorkbench follow status', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.get.mockResolvedValue({ data: [] })
    mocks.patch.mockResolvedValue({ data: {} })
    mocks.post.mockResolvedValue({ data: { id: 12 } })
    mocks.writeText.mockResolvedValue(undefined)
    mocks.confirm.mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText: mocks.writeText } })
    vi.spyOn(window, 'open').mockReturnValue({ opener: window } as Window)
  })
  afterEach(() => vi.unstubAllGlobals())

  it.each(['待筛选', '未私信', '已回复', '已移出'])('copies the script and preserves the %s workflow', async (followStatus) => {
    const workbench = useMessageWorkbench({ router: { push: mocks.push }, settings: ref({}), refreshRelated: vi.fn() })
    await workbench.messageCustomer({
      lead_id: 7, script: '当前话术', profile_url: 'https://example.com/customer',
      follow_status: followStatus, screening_status: followStatus === '待筛选' ? '待筛选' : '目标客户',
    })

    expect(mocks.writeText).toHaveBeenCalledWith('当前话术')
    expect(window.open).toHaveBeenCalledWith('https://example.com/customer', '_blank')
    // 准备动作不代表平台实际发送，所有跟进状态保持不变。
    expect(mocks.patch).not.toHaveBeenCalled()
  })
  it('records actual sending only after the user confirms', async () => {
    const workbench = useMessageWorkbench({ router: { push: mocks.push }, settings: ref({}), refreshRelated: vi.fn() })
    await workbench.confirmMessageCustomer({ lead_id: 7, follow_status: '未私信' })
    expect(mocks.confirm).toHaveBeenCalledOnce()
    expect(mocks.patch).toHaveBeenCalledWith('/overview/customers/7/follow-status', {
      follow_status: '已私信', record_message_attempt: true, note: '人工确认已发送私信',
    })
  })
  it('does not record sending when confirmation is cancelled, including the status dropdown', async () => {
    mocks.confirm.mockRejectedValue('cancel')
    const workbench = useMessageWorkbench({ router: { push: mocks.push }, settings: ref({}), refreshRelated: vi.fn() })
    await workbench.updateFollowStatus({ lead_id: 7, follow_status: '未私信' }, '已私信')
    expect(mocks.patch).not.toHaveBeenCalled()
  })
  it('selects the requested customer detail by its persistent lead identity', async () => {
    mocks.get.mockResolvedValue({ data: { customer: { lead_id: 7 }, sources: [], events: [] } })
    const workbench = useMessageWorkbench({ router: { push: mocks.push }, settings: ref({}), refreshRelated: vi.fn() })
    await workbench.selectCustomer('7')
    expect(mocks.get).toHaveBeenCalledWith('/message-workbench/customers/7')
    expect(workbench.detail.value.customer.lead_id).toBe(7)
  })
  it.each([['dy', 'prepare'], ['dy', 'fill-only'], ['xhs', 'prepare'], ['xhs', 'fill-only']])('opens a %s draft through %s without sending or changing follow status', async (platform, mode) => {
    const workbench = useMessageWorkbench({ router: { push: mocks.push }, settings: ref({ auto_dm_fill_only: true }), refreshRelated: vi.fn() })
    const customer = { lead_id: 7, platform, account_id: 'brand', script: '预填话术' }
    if (mode === 'prepare') await workbench.messageCustomer(customer)
    else await workbench.autoMessageCustomer(customer)
    expect(mocks.post).toHaveBeenCalledTimes(1)
    expect(mocks.post).toHaveBeenCalledWith('/message-inbox/conversations', { account_id: 'brand', lead_id: 7 })
    expect(mocks.push).toHaveBeenCalledWith({ path: '/message-inbox', query: { platform, account_id: 'brand', conversation: '12', draft: '预填话术' } })
    expect(window.open).not.toHaveBeenCalled()
    expect(mocks.patch).not.toHaveBeenCalled()
  })
  it('submits XHS single and batch messages through the existing endpoints and rejects Kuaishou', async () => {
    const workbench = useMessageWorkbench({ router: { push: mocks.push }, settings: ref({}), refreshRelated: vi.fn() })
    await workbench.autoMessageCustomer({ lead_id: 8, platform: 'xhs', account_id: 'xhs-brand', script: '小红书话术' })
    expect(mocks.post).toHaveBeenCalledWith('/message-workbench/customers/8/auto-message', {
      dry_run: false, message_script: '小红书话术', script_label: 'AI话术', account_id: 'xhs-brand',
    })
    const batch = { platform: 'xhs', keyword: '设备', count: 2, account_id: 'xhs-brand' }
    await workbench.startBatch(batch)
    expect(mocks.post).toHaveBeenCalledWith('/message-workbench/auto-message-batches', batch)
    mocks.post.mockClear()
    await workbench.autoMessageCustomer({ lead_id: 9, platform: 'ks', script: '快手话术' })
    await workbench.startBatch({ platform: 'ks', keyword: '设备' })
    expect(mocks.post).not.toHaveBeenCalled()
  })
})
