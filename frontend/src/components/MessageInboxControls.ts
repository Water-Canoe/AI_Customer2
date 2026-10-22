import { defineComponent, h, type PropType } from 'vue'
import type { PlatformAccount } from '../shared/types'

export type InboxPlatform = 'dy' | 'xhs'
export type ConnectionStatus = { state: string; receive_ready: boolean; send_ready: boolean; error: string; profile_error?: string; history_scope: 'local' }

// 两个平台共用账号卡片和连接操作，平台协议不进入页面控件。
export const MessageInboxControls = defineComponent({
  props: {
    platform: { type: String as PropType<InboxPlatform>, required: true },
    accountId: { type: String, required: true },
    accounts: { type: Array as PropType<PlatformAccount[]>, required: true },
    status: { type: Object as PropType<ConnectionStatus>, required: true },
    error: { type: String, default: '' },
    busy: { type: Boolean, default: false },
  },
  emits: ['platform-change', 'account-change', 'action', 'manage'],
  setup(props, { emit }) {
    return () => h('section', { class: 'panel inbox-account-bar' }, [
      h('div', { class: 'inbox-account-heading' }, [h('h2', '私信收件箱'), h('p', { class: 'note' }, '查看本系统保存的消息，接收新私信并手动回复。')]),
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
        h('button', { disabled: !props.accountId || props.busy || ['syncing', 'logging_in'].includes(props.status.state), title: '从现有用户登录会话更新认证材料，可能打开认证浏览器', onClick: () => emit('action', 'sync-auth') }, props.busy ? '处理中…' : '同步登录认证'),
        ['connected', 'connecting', 'reconnecting'].includes(props.status.state)
          ? h('button', { disabled: props.busy, onClick: () => emit('action', 'disconnect') }, '断开接收')
          : h('button', { class: 'primary-action', disabled: !props.status.receive_ready || props.busy, onClick: () => emit('action', 'connect') }, '连接接收'),
        h('button', { onClick: () => emit('manage') }, '账号管理'),
      ]),
      props.error || props.status.error ? h('p', { class: 'operations-error', role: 'alert' }, props.error || props.status.error) : null,
      props.status.profile_error ? h('p', { class: 'operations-error', role: 'alert' }, props.status.profile_error) : null,
      h('p', { class: 'inbox-scope note' }, '当前展示本地消息记录；尚未同步接入前的全部历史或离线期间遗漏的消息。'),
    ])
  },
})

function connectionLabel(state: string) { return ({ logging_in: '登录中，私信已暂停', syncing: '正在同步认证', connected: '接收已连接', connecting: '正在连接', reconnecting: '正在自动重连', disconnecting: '正在断开', disconnected: '接收未连接', expired: '接收认证已失效', error: '接收已停止' } as Record<string, string>)[state] || '接收未连接' }
