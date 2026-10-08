import { flushPromises, mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
import MessageSettingsPage from './MessageSettingsPage'

vi.mock('vue-router', () => ({ onBeforeRouteLeave: () => undefined, onBeforeRouteUpdate: () => undefined }))
vi.mock('../shared/api', () => ({ api: { get: vi.fn(async () => ({ data: {} })) } }))

it('saves the startup connection switch and preserves unsaved edits during refresh', async () => {
  const wrapper = mount(MessageSettingsPage, { props: { settings: { message_inbox_auto_connect: false, auto_dm_fill_only: false } } })
  await flushPromises()
  const toggle = wrapper.findAll('input[type="checkbox"]')[0]
  expect((toggle.element as HTMLInputElement).checked).toBe(false)
  await toggle.setValue(true)
  await wrapper.setProps({ settings: { message_inbox_auto_connect: false, auto_dm_fill_only: false } })
  expect((toggle.element as HTMLInputElement).checked).toBe(true)
  await wrapper.findAll('button').find(button => button.text() === '保存私信设置')!.trigger('click')
  expect(wrapper.emitted('save')?.[0][0]).toMatchObject({ message_inbox_auto_connect: true })
  expect(wrapper.text()).toContain('下次后端启动时生效')
  await wrapper.setProps({ settingsSaveRevision: 1 })
  expect((toggle.element as HTMLInputElement).checked).toBe(false)
  wrapper.unmount()
})
