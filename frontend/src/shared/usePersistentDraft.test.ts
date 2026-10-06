import { defineComponent, h, reactive } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { usePersistentDraft } from './usePersistentDraft'

const requests = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), delete: vi.fn(), leave: undefined as any }))
vi.mock('./api', () => ({ api: requests }))
vi.mock('vue-router', () => ({ onBeforeRouteLeave: (callback: any) => { requests.leave = callback } }))

function editor() {
  let draft: ReturnType<typeof usePersistentDraft>
  const form = reactive({ title: '' })
  const wrapper = mount(defineComponent({
    setup() {
      draft = usePersistentDraft({ kind: 'video', key: 'work-1', getPayload: () => ({ title: form.title }), applyPayload: value => { form.title = value.title }, delay: 500 })
      return () => h('span', form.title)
    },
  }))
  return { wrapper, form, draft: draft! }
}

describe('persistent editing drafts', () => {
  afterEach(() => { vi.clearAllMocks(); vi.useRealTimers() })

  it('restores a draft without saving it again, then serializes overlapping writes', async () => {
    requests.get.mockResolvedValue({ data: { exists: true, payload: { title: '原稿' }, updated_at: 't0' } })
    let finishFirst: (value: any) => void
    requests.put.mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve }))
    requests.put.mockResolvedValueOnce({ data: { updated_at: 't2' } })
    const { wrapper, form, draft } = editor()
    await draft.load()
    expect(form.title).toBe('原稿')
    expect(requests.put).not.toHaveBeenCalled()
    form.title = '第一次修改'
    const first = draft.save()
    await flushPromises()
    form.title = '第二次修改'
    const second = draft.save()
    await flushPromises()
    expect(requests.put).toHaveBeenCalledTimes(1)
    finishFirst!({ data: { updated_at: 't1' } })
    await first
    await second
    expect(requests.put.mock.calls.map(call => call[1].payload.title)).toEqual(['第一次修改', '第二次修改'])
    expect(draft.status.value).toBe('saved')
    wrapper.unmount()
  })

  it('flushes before leaving and blocks navigation on an unsaved write failure', async () => {
    requests.get.mockResolvedValue({ data: { exists: false, payload: {}, updated_at: null } })
    requests.put.mockRejectedValue(new Error('网络中断'))
    const { wrapper, form, draft } = editor()
    await draft.load()
    form.title = '尚未保存的内容'
    expect(await requests.leave()).toBe(false)
    expect(draft.status.value).toBe('error')
    expect(form.title).toBe('尚未保存的内容')
    requests.put.mockResolvedValue({ data: { updated_at: 'recovered' } })
    expect(await requests.leave()).toBe(true)
    wrapper.unmount()
  })

  it('deletes only after an in-flight save and does not recreate an empty draft after submission', async () => {
    requests.get.mockResolvedValue({ data: { exists: false, payload: {}, updated_at: null } })
    let finish: (value: any) => void
    requests.put.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    requests.delete.mockResolvedValue({ data: {} })
    const { wrapper, form, draft } = editor()
    await draft.load()
    form.title = '提交内容'
    const save = draft.save()
    await flushPromises()
    const clear = draft.clear()
    await flushPromises()
    expect(requests.delete).not.toHaveBeenCalled()
    finish!({ data: { updated_at: 't1' } })
    await save
    await clear
    form.title = ''
    await draft.save()
    expect(requests.put).toHaveBeenCalledTimes(1)
    expect(requests.delete).toHaveBeenCalledWith('/drafts/video/work-1')
    wrapper.unmount()
  })

  it('读取期间阻止离页，读取失败后的输入必须恢复保存才能离开', async () => {
    let fail!: (error: Error) => void
    requests.get.mockImplementationOnce(() => new Promise((_, reject) => { fail = reject }))
    const { wrapper, form, draft } = editor()
    const loading = draft.load().catch(() => undefined)
    await flushPromises()
    expect(await requests.leave()).toBe(false)
    form.title = '读取期间开始写的内容'
    fail(new Error('暂时断线'))
    await loading
    expect(await requests.leave()).toBe(false)
    expect(form.title).toBe('读取期间开始写的内容')
    requests.get.mockResolvedValue({ data: { exists: true, payload: { title: '服务器旧稿' }, updated_at: 't0' } })
    requests.put.mockResolvedValue({ data: { updated_at: 't1' } })
    await draft.load()
    expect(form.title).toBe('读取期间开始写的内容')
    expect(await requests.leave()).toBe(true)
    expect(requests.put).toHaveBeenLastCalledWith('/drafts/video/work-1', { payload: { title: '读取期间开始写的内容' } })
    wrapper.unmount()
  })
})
