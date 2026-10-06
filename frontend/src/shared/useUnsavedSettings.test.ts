import { defineComponent, h, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { useUnsavedSettings } from './useUnsavedSettings'

const mocks = vi.hoisted(() => ({ confirm: vi.fn(), leave: undefined as any, update: undefined as any }))
vi.mock('element-plus', () => ({ ElMessageBox: { confirm: mocks.confirm } }))
vi.mock('vue-router', () => ({
  onBeforeRouteLeave: (guard: any) => { mocks.leave = guard },
  onBeforeRouteUpdate: (guard: any) => { mocks.update = guard },
}))

describe('未保存设置离页保护', () => {
  it('无修改直接离开，有修改时保留输入或明确放弃，同组件切换同样受保护', async () => {
    const dirty = ref(false)
    const wrapper = mount(defineComponent({ setup() { useUnsavedSettings(() => dirty.value); return () => h('div') } }))
    expect(await mocks.leave()).toBe(true)
    expect(mocks.confirm).not.toHaveBeenCalled()
    dirty.value = true
    mocks.confirm.mockRejectedValueOnce('cancel')
    expect(await mocks.leave()).toBe(false)
    expect(dirty.value).toBe(true)
    mocks.confirm.mockResolvedValueOnce('confirm')
    expect(await mocks.update()).toBe(true)
    wrapper.unmount()
  })
})
