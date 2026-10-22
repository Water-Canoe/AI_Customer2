import { defineComponent, h, type PropType } from 'vue'
import type { PlatformAccount } from '../shared/types'

export type InboxPlatform = 'dy' | 'xhs'
export type SdkLoginStatus = { state: 'idle' | 'starting' | 'qrcode' | 'verification' | 'online' | 'error' | 'cancelled'; image_base64: string; verification_url: string; error: string }
export type ConnectionStatus = { state: string; receive_ready: boolean; send_ready: boolean; error: string; profile_error?: string; history_scope: 'local' | 'platform'; sdk_login?: SdkLoginStatus }

// 两个平台共用账号卡片和连接操作，平台协议不进入页面控件。
export const MessageInboxControls = defineComponent({
  props: {
    platform: { type: String as PropType<InboxPlatform>, required: true },
    accountId: { type: String, required: true },
    accounts: { type: Array as PropType<PlatformAccount[]>, required: true },
    status: { type: Object as PropType<ConnectionStatus>, required: true },
    error: { type: String, default: '' },
    busy: { type: Boolean, default: false },
    historyBusy: { type: Boolean, default: false },
  },
  emits: ['platform-change', 'account-change', 'action', 'manage'],
  setup(props, { emit }) {
    return () => {
      const login = props.platform === 'dy' ? props.status.sdk_login : undefined
      const loggingIn = Boolean(login && ['starting', 'qrcode', 'verification'].includes(login.state))
      const authBusy = props.busy || loggingIn || ['syncing', 'logging_in'].includes(props.status.state)
      return h('section', { class: 'panel inbox-account-bar' }, [
      h('div', { class: 'inbox-account-heading' }, [h('h2', '私信收件箱'), h('p', { class: 'note' }, '查看消息记录，接收新私信并手动回复。')]),
      h('div', { class: 'toolbar' }, [
        h('label', { class: 'inbox-account-picker inbox-platform-picker' }, [h('span', '平台'), h('select', {
          value: props.platform, 'aria-label': '私信平台', onChange: (event: Event) => emit('platform-change', (event.target as HTMLSelectElement).value),
        }, [h('option', { value: 'dy' }, '抖音'), h('option', { value: 'xhs' }, '小红书')])]),
        h('label', { class: 'inbox-account-picker' }, [h('span', '私信账号'), h('select', {
          value: props.accountId, 'aria-label': '私信账号', onChange: (event: Event) => emit('account-change', (event.target as HTMLSelectElement).value),
        }, [!props.accounts.length ? h('option', { value: '' }, '暂无可用账号') : null,
          ...props.accounts.map(item => h('option', { value: item.id }, item.name)),
        ])]),
        h('span', { class: ['inbox-connection-status', `is-${props.status.state}`], role: 'status' }, connectionLabel(props.status.state)),
        h('button', { disabled: !props.accountId || authBusy, title: props.platform === 'dy' ? '恢复本账号已保存的私信认证；首次接入请扫码登录私信' : '从现有用户登录会话更新认证材料，可能打开认证浏览器', onClick: () => emit('action', 'sync-auth') }, props.platform === 'dy' ? '恢复私信认证' : '同步登录认证'),
        props.platform === 'dy' ? h('button', { disabled: !props.accountId || authBusy, onClick: () => emit('action', 'sdk-login') }, '扫码登录私信') : null,
        ['connected', 'connecting', 'reconnecting'].includes(props.status.state)
          ? h('button', { disabled: authBusy, onClick: () => emit('action', 'disconnect') }, '断开接收')
          : h('button', { class: 'primary-action', disabled: !props.status.receive_ready || authBusy, onClick: () => emit('action', 'connect') }, '连接接收'),
        props.platform === 'dy' ? h('button', { disabled: !props.status.receive_ready || authBusy || props.historyBusy, onClick: () => emit('action', 'sync-history') }, props.historyBusy ? '同步历史中…' : '同步平台历史') : null,
        h('button', { onClick: () => emit('manage') }, '账号管理'),
      ]),
      login && !['idle', 'cancelled'].includes(login.state) ? h('div', { class: 'inbox-sdk-login', 'aria-label': '抖音私信扫码登录' }, [
        h('p', { class: 'note', role: 'status' }, ({ starting: '正在获取私信登录二维码…', qrcode: '请使用抖音 App 扫码并确认登录。', verification: '请打开验证页面完成登录验证。', online: '私信扫码登录成功。', error: '私信扫码登录失败，请重新扫码。' } as Record<string, string>)[login.state]),
        ['qrcode', 'verification'].includes(login.state) && login.image_base64 ? h('img', { class: 'inbox-sdk-qrcode', src: 'data:image/png;base64,' + login.image_base64, alt: '抖音私信登录二维码' }) : null,
        // 验证地址仅允许网页协议，避免把异常认证数据当成可执行链接。
        login.state === 'verification' && /^https?:\/\//i.test(login.verification_url) ? h('a', { href: login.verification_url, target: '_blank', rel: 'noopener noreferrer' }, '打开登录验证页面') : null,
        login.error ? h('p', { class: 'operations-error', role: 'alert' }, login.error) : null,
        loggingIn ? h('button', { disabled: props.busy, onClick: () => emit('action', 'sdk-login/cancel') }, '取消扫码登录') : null,
      ]) : null,
      props.error || props.status.error ? h('p', { class: 'operations-error', role: 'alert' }, props.error || props.status.error) : null,
      props.status.profile_error ? h('p', { class: 'operations-error', role: 'alert' }, props.status.profile_error) : null,
      h('p', { class: 'inbox-scope note' }, props.platform === 'dy'
        ? '当前展示已保存的消息；点击“同步平台历史”拉取抖音允许访问的历史记录，完整范围以平台返回为准。'
        : '当前展示本地消息记录；尚未同步接入前的全部历史或离线期间遗漏的消息。'),
    ])
    }
  },
})

function connectionLabel(state: string) { return ({ logging_in: '登录中，私信已暂停', syncing: '正在同步认证', connected: '接收已连接', connecting: '正在连接', reconnecting: '正在自动重连', disconnecting: '正在断开', disconnected: '接收未连接', expired: '接收认证已失效', error: '接收已停止' } as Record<string, string>)[state] || '接收未连接' }
