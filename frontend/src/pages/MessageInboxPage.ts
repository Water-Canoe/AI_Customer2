import { computed, defineComponent, h, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../shared/api'
import type { Dict, PlatformAccount } from '../shared/types'
import { createAutoSyncController } from '../composables/autoSync'
import '../styles/operations.css'
import '../styles/message-inbox.css'

type Conversation = {
  id: number; account_id: string; peer_user_id: string; nickname: string; avatar: string; last_message: string;
  last_message_at: string | null; unread_count: number; lead_id: number | null;
}
type Message = { id: number; direction: 'incoming' | 'outgoing'; content: string; message_type: string | number; status: string; error?: string; created_at: string }
type ConnectionStatus = { state: string; receive_ready: boolean; send_ready: boolean; error: string; profile_error?: string; history_scope: 'local' }
const emptyStatus = (): ConnectionStatus => ({ state: 'disconnected', receive_ready: false, send_ready: false, error: '', history_scope: 'local' })

export default defineComponent({
  name: 'MessageInboxPage',
  props: { refreshSeq: { type: Number, default: 0 } },
  setup(props) {
    const route = useRoute()
    const router = useRouter()
    const accounts = ref<PlatformAccount[]>([])
    const accountId = ref('')
    const conversationId = ref(0)
    const conversations = ref<Conversation[]>([])
    const messages = ref<Message[]>([])
    const status = ref<ConnectionStatus>(emptyStatus())
    const customer = ref<Dict | null>(null)
    const search = ref('')
    const appliedSearch = ref('')
    const error = ref('')
    const actionBusy = ref(false)
    const sending = ref(false)
    const loadingEarlier = ref(false)
    const hasMore = ref(false)
    const transcript = ref<HTMLElement | null>(null)
    const unavailableAvatars = ref(new Set<string>())
    // 按账号和会话保留未发送文字，切换聊天或页签不会串用草稿。
    const drafts = ref<Record<string, string>>({})
    const draftKey = computed(() => `${accountId.value}:${conversationId.value}`)
    const draft = computed(() => drafts.value[draftKey.value] || '')
    const selected = computed(() => conversations.value.find(item => item.id === conversationId.value))
    let active = true
    let revision = 0
    let customerKey = ''
    let activatedOnce = false

    const refresh = createAutoSyncController({ interval: () => 3000, sync: async reason => {
      if (!active) return
      let token = revision
      try {
        if (reason === 'route' || !accounts.value.length) {
          const { data } = await api.get<PlatformAccount[]>('/accounts', { params: { feature: 'message', platform: 'dy' } })
          if (!current(token)) return
          accounts.value = data.filter(item => item.enabled && item.features?.includes('message'))
          if (!accounts.value.some(item => String(item.id) === accountId.value)) {
            const preferred = accounts.value.find(item => item.default_features?.includes('message')) || accounts.value[0]
            setSelection(String(preferred?.id || ''), 0)
            token = revision
          }
        }
        if (!accountId.value || !current(token)) return
        const account = accountId.value
        const query = appliedSearch.value
        const results = await Promise.all([
          api.get<ConnectionStatus>(accountPath(account) + '/status'),
          api.get<Conversation[]>('/message-inbox/conversations', { params: { account_id: account, query } }),
        ])
        if (!current(token) || account !== accountId.value || query !== appliedSearch.value) return
        status.value = results[0].data
        conversations.value = results[1].data
        error.value = ''
        if (selected.value) {
          await loadMessages(token)
          await loadCustomer(token)
        }
      } catch (cause: any) {
        if (current(token)) error.value = apiError(cause, '私信收件箱加载失败')
      }
    } })

    function current(token: number) { return active && token === revision }
    // 会话列表和聊天标题共用头像；未获取或加载失败时仅显示占位。
    function avatar(item: Conversation) {
      const available = item.avatar && !unavailableAvatars.value.has(item.avatar)
      return h('span', { class: 'inbox-avatar', 'aria-hidden': 'true', title: available ? undefined : '头像暂不可用' }, available
        ? h('img', { src: item.avatar, alt: '', loading: 'lazy', onError: () => unavailableAvatars.value.add(item.avatar) })
        : h('span', { class: 'inbox-avatar-placeholder' }, '头像'))
    }
    function accountPath(id = accountId.value) { return '/message-inbox/accounts/' + encodeURIComponent(id) }
    function setSelection(account: string, conversation: number) {
      if (account === accountId.value && conversation === conversationId.value) return
      revision += 1
      if (account !== accountId.value) {
        accountId.value = account
        conversations.value = []
        status.value = emptyStatus()
      }
      conversationId.value = conversation
      messages.value = []
      hasMore.value = false
      customer.value = null
      customerKey = ''
      error.value = ''
    }
    function navigate(account: string, conversation = 0) {
      void router.replace({ path: '/message-inbox', query: { ...(account ? { account_id: account } : {}), ...(conversation ? { conversation: String(conversation) } : {}) } })
    }
    function restoreRouteDraft() {
      const text = route.query.draft
      if (typeof text !== 'string' || !accountId.value || !conversationId.value) return
      if (!drafts.value[draftKey.value]) drafts.value[draftKey.value] = text
      else ElMessage.warning('已保留当前会话的未发送内容')
      // 草稿仅导入一次，随后移出地址，避免刷新和页签恢复覆盖编辑。
      const { draft: _draft, ...query } = route.query
      void router.replace({ path: '/message-inbox', query })
    }
    async function loadMessages(token: number) {
      const account = accountId.value
      const conversation = conversationId.value
      const pane = transcript.value
      const followBottom = !messages.value.length || !pane || pane.scrollHeight - pane.scrollTop - pane.clientHeight < 80
      const { data } = await api.get<{ messages: Message[]; has_more: boolean }>(`/message-inbox/conversations/${conversation}/messages`, { params: { account_id: account, limit: 50 } })
      if (!current(token)) return
      const firstLoad = !messages.value.length
      messages.value = mergeMessages(messages.value, data.messages)
      if (firstLoad) hasMore.value = data.has_more
      await nextTick()
      if (current(token) && followBottom && transcript.value) transcript.value.scrollTop = transcript.value.scrollHeight
      const count = selected.value?.unread_count || 0
      if (count > 0) {
        await api.post(`/message-inbox/conversations/${conversation}/read`, null, { params: { account_id: account, up_to_id: messages.value[messages.value.length - 1]?.id } })
        if (current(token)) {
          if (selected.value) selected.value.unread_count = 0
        }
      }
    }
    async function loadCustomer(token: number) {
      const lead = selected.value?.lead_id
      const key = `${accountId.value}:${lead || ''}`
      if (key === customerKey) return
      customer.value = null
      if (!lead) { customerKey = key; return }
      const { data } = await api.get(`/message-workbench/customers/${lead}`)
      if (current(token)) { customer.value = data.customer; customerKey = key }
    }
    async function earlier() {
      if (loadingEarlier.value || !messages.value.length) return
      loadingEarlier.value = true
      const token = revision
      const pane = transcript.value
      const height = pane?.scrollHeight || 0
      const top = pane?.scrollTop || 0
      try {
        const { data } = await api.get<{ messages: Message[]; has_more: boolean }>(`/message-inbox/conversations/${conversationId.value}/messages`, { params: { account_id: accountId.value, before_id: messages.value[0].id, limit: 50 } })
        if (!current(token)) return
        messages.value = mergeMessages(data.messages, messages.value)
        hasMore.value = data.has_more
        await nextTick()
        if (current(token) && transcript.value) transcript.value.scrollTop = top + transcript.value.scrollHeight - height
      } catch (cause: any) { if (current(token)) ElMessage.error(apiError(cause, '更早消息加载失败')) }
      finally { loadingEarlier.value = false }
    }
    async function accountAction(action: 'connect' | 'disconnect' | 'sync-auth') {
      if (!accountId.value || actionBusy.value) return
      actionBusy.value = true
      const token = revision
      const account = accountId.value
      try {
        await api.post(accountPath(account) + '/' + action)
        if (current(token)) {
          if (action === 'sync-auth') ElMessage.success('已更新私信认证状态')
          await refresh.trigger('route')
        }
      } catch (cause: any) { if (current(token)) ElMessage.error(apiError(cause, '私信操作失败')) }
      finally { actionBusy.value = false }
    }
    async function send() {
      const text = draft.value.trim()
      if (!text || sending.value || !selected.value || !status.value.send_ready) return
      const account = accountId.value
      const conversation = conversationId.value
      const key = draftKey.value
      const original = draft.value
      const token = revision
      sending.value = true
      try {
        const { data } = await api.post<Message>(`/message-inbox/conversations/${conversation}/messages`, { account_id: account, text })
        // 已提交和待确认都不能自动重发；保留后端给出的投递状态。
        if (drafts.value[key] === original && data.status !== 'failed') drafts.value[key] = ''
        if (current(token)) {
          messages.value = mergeMessages(messages.value, [data])
          if (data.status === 'uncertain') ElMessage.warning('发送结果待确认，请核实后再决定是否重发')
          else if (data.status === 'failed') ElMessage.error(data.error || '发送失败')
          await refresh.trigger('visible')
        }
      } catch (cause: any) { if (current(token)) { ElMessage.error(apiError(cause, '发送失败，请查看消息记录')); await refresh.trigger('visible') } }
      finally { sending.value = false }
    }

    watch(() => [route.name, route.query.account_id, route.query.conversation, route.query.draft] as const, ([name, account, conversation]) => {
      if (name !== 'message-inbox') return
      setSelection(String(account || accountId.value), Number(conversation || 0))
      restoreRouteDraft()
      if (active) void refresh.trigger('route')
    })
    watch(() => props.refreshSeq, () => { if (active) void refresh.trigger('route') })
    function start() {
      active = true
      setSelection(String(route.query.account_id || accountId.value), Number(route.query.conversation || 0))
      restoreRouteDraft()
      refresh.start()
      void refresh.trigger('route')
    }
    function stop() { active = false; revision += 1; refresh.stop() }
    onMounted(start)
    onActivated(() => { if (activatedOnce) start(); activatedOnce = true })
    onDeactivated(stop)
    onUnmounted(stop)

    return () => h('div', { class: 'workspace-page operations-page message-inbox-page' }, [
      h('section', { class: 'panel inbox-account-bar' }, [
        h('div', { class: 'inbox-account-heading' }, [h('h2', '私信收件箱'), h('p', { class: 'note' }, '查看本系统保存的消息，接收新私信并手动回复。')]),
        h('div', { class: 'toolbar' }, [
          h('label', { class: 'inbox-account-picker' }, [h('span', '私信账号'), h('select', { value: accountId.value, 'aria-label': '私信账号', onChange: (event: Event) => navigate((event.target as HTMLSelectElement).value) }, [
            !accounts.value.length ? h('option', { value: '' }, '暂无可用账号') : null,
            ...accounts.value.map(item => h('option', { value: item.id }, item.name)),
          ])]),
          h('span', { class: ['inbox-connection-status', `is-${status.value.state}`], role: 'status' }, connectionLabel(status.value.state)),
          h('button', { disabled: !accountId.value || actionBusy.value, title: '从现有用户登录会话更新认证材料，可能打开认证浏览器', onClick: () => accountAction('sync-auth') }, actionBusy.value ? '处理中…' : '同步登录认证'),
          ['connected', 'connecting', 'reconnecting'].includes(status.value.state)
            ? h('button', { disabled: actionBusy.value, onClick: () => accountAction('disconnect') }, '断开接收')
            : h('button', { class: 'primary-action', disabled: !status.value.receive_ready || actionBusy.value, onClick: () => accountAction('connect') }, '连接接收'),
          h('button', { onClick: () => router.push({ path: '/accounts', query: { feature: 'message', platform: 'dy', return_to: route.fullPath } }) }, '账号管理'),
        ]),
        error.value || status.value.error ? h('p', { class: 'operations-error', role: 'alert' }, error.value || status.value.error) : null,
        status.value.profile_error ? h('p', { class: 'operations-error', role: 'alert' }, status.value.profile_error) : null,
        h('p', { class: 'inbox-scope note' }, '当前展示本地消息记录；尚未同步接入前的全部历史或离线期间遗漏的消息。'),
      ]),
      h('section', { class: 'panel inbox-layout' }, [
        h('aside', { class: 'inbox-conversations', 'aria-label': '私信会话' }, [
          h('form', { class: 'inbox-search', onSubmit: (event: Event) => { event.preventDefault(); appliedSearch.value = search.value.trim(); revision += 1; void refresh.trigger('route') } }, [
            h('input', { value: search.value, placeholder: '搜索会话', 'aria-label': '搜索会话', onInput: (event: Event) => search.value = (event.target as HTMLInputElement).value }),
            h('button', { type: 'submit' }, '搜索'),
          ]),
          h('div', { class: 'inbox-conversation-list' }, conversations.value.length ? conversations.value.map(item => h('button', {
            type: 'button', class: ['inbox-conversation', item.id === conversationId.value ? 'is-active' : ''], 'aria-pressed': item.id === conversationId.value,
            onClick: () => navigate(accountId.value, item.id),
          }, [avatar(item), h('div', { class: 'inbox-conversation-summary' }, [h('div', { class: 'inbox-conversation-title' }, [h('strong', { title: item.nickname || item.peer_user_id }, item.nickname || item.peer_user_id), item.unread_count ? h('span', { class: 'inbox-unread', 'aria-label': `${item.unread_count} 条未读` }, String(item.unread_count)) : null]),
            h('p', item.last_message || '暂无消息'), h('time', item.last_message_at || ''),
          ]),
          ])) : h('p', { class: 'inbox-empty' }, accountId.value ? '暂无本地会话，可从客户详情打开会话。' : '请先配置抖音私信账号。')),
        ]),
        h('div', { class: 'inbox-chat' }, selected.value ? [
          h('header', { class: 'inbox-chat-header' }, [avatar(selected.value), h('strong', { title: selected.value.nickname || selected.value.peer_user_id }, selected.value.nickname || selected.value.peer_user_id), h('span', { class: 'note' }, '抖音私信')]),
          h('div', { class: 'inbox-transcript', ref: transcript, role: 'log', 'aria-label': '聊天记录' }, [
            hasMore.value ? h('button', { class: 'inbox-earlier secondary-action', disabled: loadingEarlier.value, onClick: earlier }, loadingEarlier.value ? '加载中…' : '加载更早记录') : null,
            ...messages.value.map(item => h('article', { key: item.id, class: ['inbox-message', `is-${item.direction}`] }, [
              h('div', { class: 'inbox-message-content' }, item.content || '暂不支持展示此消息内容'),
              h('div', { class: 'inbox-message-meta' }, [h('time', item.created_at), item.direction === 'outgoing' ? h('span', { class: `inbox-delivery is-${item.status}`, title: item.error || '' }, deliveryLabel(item.status)) : null]),
              item.error ? h('small', { class: 'operations-error' }, item.error) : null,
            ])),
            !messages.value.length ? h('p', { class: 'inbox-empty' }, '暂无本地聊天记录。') : null,
          ]),
          h('form', { class: 'inbox-composer', onSubmit: (event: Event) => { event.preventDefault(); void send() } }, [
            h('textarea', { value: draft.value, rows: 3, maxlength: 2000, placeholder: '输入文字消息', 'aria-label': '私信内容', onInput: (event: Event) => drafts.value[draftKey.value] = (event.target as HTMLTextAreaElement).value,
              onKeydown: (event: KeyboardEvent) => { if (event.key === 'Enter' && event.ctrlKey && !event.isComposing) { event.preventDefault(); void send() } } }),
            h('div', { class: 'inbox-composer-actions' }, [h('span', { class: 'note' }, status.value.send_ready ? 'Ctrl + Enter 发送 · 已提交不代表对方已读' : '请先同步有效的私信发送认证'),
              h('button', { type: 'submit', class: 'primary-action', disabled: sending.value || !draft.value.trim() || !status.value.send_ready }, sending.value ? '发送中…' : '发送'),
            ]),
          ]),
        ] : h('div', { class: 'inbox-empty inbox-select-hint' }, '选择左侧会话查看消息')),
        h('aside', { class: 'inbox-customer', 'aria-label': '关联客户' }, [h('h3', '关联客户'),
          customer.value ? [h('strong', customer.value.nickname || selected.value?.nickname), h('p', `意向：${customer.value.intention || customer.value.intention_level || '未评估'}`), h('p', `跟进：${customer.value.follow_status || '未私信'}`),
            customer.value.keyword_text ? h('p', { class: 'note' }, customer.value.keyword_text) : null,
            h('button', { onClick: () => router.push({ path: '/message-workbench', query: { customer: String(selected.value?.lead_id), status: '全部' } }) }, '查看客户详情'),
          ] : h('p', { class: 'note' }, selected.value ? '当前会话尚无客户关联。' : '选择会话后查看客户信息。'),
        ]),
      ]),
    ])
  },
})

function apiError(error: any, fallback: string) { return typeof error?.response?.data?.detail === 'string' ? error.response.data.detail : fallback }
function mergeMessages(previous: Message[], incoming: Message[]) { return [...new Map([...previous, ...incoming].map(item => [item.id, item])).values()].sort((left, right) => left.id - right.id) }
function connectionLabel(state: string) { return ({ connected: '接收已连接', connecting: '正在连接', reconnecting: '正在重连', disconnected: '接收未连接', error: '连接异常' } as Record<string, string>)[state] || '接收未连接' }
function deliveryLabel(status: string) { return ({ sending: '发送中', accepted: '已提交', failed: '发送失败', uncertain: '结果待确认', received: '已接收' } as Record<string, string>)[status] || '结果待确认' }
