import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import SettingsPage from './SettingsPage'

vi.mock('vue-router', () => ({ onBeforeRouteLeave: () => undefined, onBeforeRouteUpdate: () => undefined }))

vi.mock('../components/ui/SplitPane', async () => {
  const { defineComponent, h } = await import('vue')
  return { SplitPane: defineComponent({ setup: (_, { slots }) => () => h('div', [slots.default?.(), slots.side?.()]) }) }
})

describe('AI 密钥设置', () => {
  it('keeps the configured secret on ordinary save and sends only explicit replacement or clearing', async () => {
    const wrapper = mount(SettingsPage, { props: { settings: { ai_api_key: '', ai_api_key_configured: true, ai_model: 'model' }, env: {} } })
    const password = wrapper.get('input[type="password"]')
    const save = () => wrapper.findAll('button').find(button => button.text() === '保存设置')!.trigger('click')
    expect((password.element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('密钥已配置')
    await save(); await new Promise(resolve => setTimeout(resolve, 1)); await flushPromises()
    expect(wrapper.emitted('save')?.[0][0]).not.toHaveProperty('ai_api_key')
    expect(wrapper.emitted('save')?.[0][0]).not.toHaveProperty('ai_api_key_configured')
    expect(wrapper.emitted('save')?.[0][1]).toEqual([])

    await password.setValue('replacement-key')
    await wrapper.setProps({ settings: { ai_api_key: '', ai_api_key_configured: true, ai_model: 'new-model' } })
    expect((password.element as HTMLInputElement).value).toBe('replacement-key')
    await save(); await new Promise(resolve => setTimeout(resolve, 1)); await flushPromises()
    expect(wrapper.emitted('save')?.[1]).toEqual([expect.objectContaining({ ai_api_key: 'replacement-key' }), []])

    await wrapper.setProps({ settingsSaveRevision: 1 })
    expect((password.element as HTMLInputElement).value).toBe('')
    await wrapper.findAll('button').find(button => button.text() === '清除已保存密钥')!.trigger('click')
    expect(wrapper.text()).toContain('保存设置后清除已存密钥')
    await save(); await new Promise(resolve => setTimeout(resolve, 1)); await flushPromises()
    expect(wrapper.emitted('save')?.[2][0]).not.toHaveProperty('ai_api_key')
    expect(wrapper.emitted('save')?.[2][0]).not.toHaveProperty('ai_api_key_configured')
    expect(wrapper.emitted('save')?.[2][1]).toEqual(['ai_api_key'])
    wrapper.unmount()
    const changes = wrapper.emitted('settings-dirty-change')!
    expect(changes[changes.length - 1]).toEqual([false])
  })
})
