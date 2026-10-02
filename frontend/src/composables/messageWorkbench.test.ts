import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import { selectDmScript, useMessageWorkbench } from './messageWorkbench'

const mocks = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn(), writeText: vi.fn() }))
vi.mock('../shared/api', () => ({ api: { get: mocks.get, patch: mocks.patch } }))
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
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
    mocks.writeText.mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText: mocks.writeText } })
    vi.spyOn(window, 'open').mockReturnValue({ opener: window } as Window)
  })
  afterEach(() => vi.unstubAllGlobals())

  it.each(['待筛选', '未私信', '已回复', '已移出'])('copies the script and preserves the %s workflow', async (followStatus) => {
    const workbench = useMessageWorkbench({ settings: ref({}), refreshRelated: vi.fn() })
    await workbench.messageCustomer({
      lead_id: 7, script: '当前话术', profile_url: 'https://example.com/customer',
      follow_status: followStatus, screening_status: followStatus === '待筛选' ? '待筛选' : '目标客户',
    })

    expect(mocks.writeText).toHaveBeenCalledWith('当前话术')
    expect(window.open).toHaveBeenCalledWith('https://example.com/customer', '_blank')
    // 当前未发送状态才流转，回复和软删除状态不被私信动作回退。
    if (['待筛选', '未私信'].includes(followStatus)) {
      expect(mocks.patch).toHaveBeenCalledWith('/overview/customers/7/follow-status', expect.objectContaining({
        follow_status: '已私信', record_message_attempt: true,
      }))
    } else {
      expect(mocks.patch).not.toHaveBeenCalled()
    }
  })
})
