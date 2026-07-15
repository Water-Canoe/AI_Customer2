import { defineComponent, h, type PropType } from 'vue'

import type { Dict } from '../../shared/types'

export const LicenseDialog = defineComponent({
  name: 'LicenseDialog',
  props: {
    open: { type: Boolean, required: true },
    loading: { type: Boolean, default: false },
    checking: { type: Boolean, default: false },
    info: { type: Object as PropType<Dict>, default: () => ({}) },
    code: { type: String, default: '' },
    placeholder: { type: String, default: '输入产品授权码' },
  },
  emits: ['close', 'update:code', 'save', 'check', 'copy-device'],
  setup(props, { emit }) {
    return () => {
      if (!props.open) return null
      const status = String(props.info.status || 'unconfigured')
      const statusText = licenseStatusText(status, Boolean(props.info.authorized))
      return h('div', { class: 'license-modal-backdrop', onClick: () => emit('close') }, [
        h('div', { class: 'license-modal', onClick: (event: Event) => event.stopPropagation() }, [
          h('div', { class: 'license-modal-head' }, [
            h('div', [
              h('h3', '授权与设备'),
              h('p', '授权码可修改，设备码由本机生成且不可编辑')
            ]),
            h('button', { type: 'button', class: 'icon-button', title: '关闭授权窗口', 'aria-label': '关闭授权窗口', onClick: () => emit('close') }, '×')
          ]),
          props.loading
            ? h('div', { class: 'diagnostic-empty' }, '正在读取授权信息')
            : h('div', { class: 'license-form' }, [
              h('label', [
                h('span', '授权码'),
                h('input', {
                  value: props.code,
                  placeholder: props.placeholder,
                  onInput: (event: Event) => emit('update:code', (event.target as HTMLInputElement).value)
                })
              ]),
              h('label', [
                h('span', '设备码'),
                h('div', { class: 'readonly-input-row' }, [
                  h('input', {
                    value: props.info.device_code || '',
                    readonly: true,
                    title: '设备码由本机后端生成，不支持手动修改'
                  }),
                  h('button', { class: 'secondary-action compact-action', onClick: () => emit('copy-device') }, '复制')
                ])
              ]),
              h('div', { class: ['license-status-card', props.info.authorized ? 'authorized' : ''] }, [
                h('strong', statusText),
                h('span', props.info.message || '尚未校验授权'),
                props.info.reason ? h('small', `原因：${props.info.reason}`) : null,
                props.info.last_checked_at || props.info.checked_at
                  ? h('small', `最近校验：${props.info.last_checked_at || props.info.checked_at}`)
                  : null,
                props.info.max_devices
                  ? h('small', `设备数：${props.info.active_device_count || 0} / ${props.info.max_devices}`)
                  : null,
                Array.isArray(props.info.entitlements) && props.info.entitlements.length
                  ? h('small', `已开通：${props.info.entitlements.map(entitlementName).join('、')}`)
                  : null,
                props.info.lease_expires_at
                  ? h('small', `离线授权有效至：${new Date(String(props.info.lease_expires_at)).toLocaleString()}`)
                  : null
              ]),
              h('div', { class: 'license-actions' }, [
                h('button', { class: 'secondary-action', disabled: props.checking, onClick: () => emit('save') }, '保存授权码'),
                h('button', { class: 'primary-action', disabled: props.checking, onClick: () => emit('check') }, props.checking ? '校验中' : '保存并校验')
              ])
            ])
        ])
      ])
    }
  }
})

function licenseStatusText(status: string, authorized: boolean) {
  if (authorized || status === 'authorized') return '授权通过'
  if (status === 'failed') return '授权失败'
  return '未校验'
}

function entitlementName(value: string) {
  const names: Record<string, string> = { lead: '拓客', traffic: '引流', content: '内容' }
  return names[value] || value
}
