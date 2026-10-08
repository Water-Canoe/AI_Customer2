import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SoftwareUpdateDialog } from './SoftwareUpdateDialog'


const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), confirm: vi.fn() }))
vi.mock('../../shared/api', () => ({ api: { get: mocks.get, post: mocks.post } }))
vi.mock('element-plus', () => ({ ElMessageBox: { confirm: mocks.confirm } }))

const offer = { phase: 'idle', current_version: '1.2.3', version: '1.2.4', notes: '修复私信\n优化启动', available: true, can_prepare: true, size: 1048576 }
let wrapper: VueWrapper | undefined

function button(text: string) {
  return wrapper!.findAll('button').find(item => item.text() === text)!
}

describe('软件更新弹窗', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    mocks.post.mockResolvedValue({ data: offer })
    mocks.confirm.mockResolvedValue(undefined)
  })
  afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.useRealTimers() })

  it('检查只展示更新；下载期间轮询，安装需另行确认', async () => {
    wrapper = mount(SoftwareUpdateDialog, { props: { open: true } })
    await flushPromises()
    expect(mocks.post).toHaveBeenCalledExactlyOnceWith('/system/check-update')
    expect(wrapper.text()).toContain('当前版本：1.2.3')
    expect(wrapper.text()).toContain('更新版本：1.2.4 · 1.0 MB')
    expect(wrapper.text()).toContain('修复私信')
    expect(mocks.confirm).not.toHaveBeenCalled()

    mocks.post.mockResolvedValueOnce({ data: { ...offer, phase: 'downloading', percent: 0, message: '下载中' } })
    mocks.get.mockResolvedValueOnce({ data: { ...offer, phase: 'downloading', percent: 45 } })
    await button('下载更新').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1000)
    expect(mocks.get).toHaveBeenCalledWith('/system/update-status')
    expect(wrapper.get('progress').attributes('value')).toBe('45')

    mocks.get.mockResolvedValueOnce({ data: { ...offer, phase: 'ready', percent: 100 } })
    await vi.advanceTimersByTimeAsync(1000)
    expect(mocks.post).not.toHaveBeenCalledWith('/system/install-update')
    mocks.confirm.mockRejectedValueOnce('cancel')
    await button('安装并重启').trigger('click')
    await flushPromises()
    expect(mocks.post).not.toHaveBeenCalledWith('/system/install-update')
    mocks.post.mockResolvedValueOnce({ data: { ok: true, message: '正在重启' } })
    await button('安装并重启').trigger('click')
    await flushPromises()
    expect(mocks.post).toHaveBeenCalledWith('/system/install-update')
    expect(wrapper.emitted('installing')).toHaveLength(1)
  })

  it('关闭仅停止页面轮询，重新打开继续后台下载，并忽略迟到响应', async () => {
    let finishOld!: (value: any) => void
    mocks.post.mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve }))
    wrapper = mount(SoftwareUpdateDialog, { props: { open: true } })
    await wrapper.setProps({ open: false })
    await vi.advanceTimersByTimeAsync(2000)
    expect(mocks.get).not.toHaveBeenCalled()
    mocks.post.mockResolvedValueOnce({ data: { ...offer, phase: 'downloading', percent: 30 } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    finishOld({ data: { ...offer, phase: 'ready', version: 'stale' } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('stale')
    expect(wrapper.get('progress').attributes('value')).toBe('30')
    mocks.get.mockResolvedValueOnce({ data: { ...offer, phase: 'downloading', percent: 60 } })
    await vi.advanceTimersByTimeAsync(1000)
    expect(wrapper.get('progress').attributes('value')).toBe('60')
    const calls = mocks.get.mock.calls.length
    wrapper.unmount()
    wrapper = undefined
    await vi.advanceTimersByTimeAsync(2000)
    expect(mocks.get).toHaveBeenCalledTimes(calls)
    expect(mocks.post.mock.calls.every(([path]) => path === '/system/check-update')).toBe(true)
  })

  it('显示接口失败原因并允许重新检查', async () => {
    mocks.post.mockRejectedValueOnce({ response: { data: { detail: '网络不可用' } } })
    wrapper = mount(SoftwareUpdateDialog, { props: { open: true } })
    await flushPromises()
    expect(wrapper.text()).toContain('网络不可用')
    await button('重新检查').trigger('click')
    await flushPromises()
    expect(button('下载更新').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('网络不可用')
    expect(mocks.post).toHaveBeenCalledTimes(2)
  })
})
