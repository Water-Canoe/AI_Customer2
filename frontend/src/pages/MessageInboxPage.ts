import { computed, defineComponent, h, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElDrawer, ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../shared/api'
import type { Dict, PlatformAccount } from '../shared/types'
import { createAutoSyncController } from '../composables/autoSync'
import { MessageInboxControls, type InboxPlatform, type ConnectionStatus, type SdkLoginStatus } from '../components/MessageInboxControls'
import { platformName } from '../shared/format'
import { renderCustomerDetail } from '../components/CustomerDetail'
import '../styles/operations.css'
import '../styles/message-inbox.css'

type Conversation = {
  id: number; account_id: string; peer_user_id: string; nickname: string; avatar: string; last_message: string;
  last_message_at: string | null; unread_count: number; lead_id: number | null;
}
type Message = { id: number; direction: 'incoming' | 'outgoing'; content: string; message_type: string | number; status: string; error?: string; sent_at: string; created_at: string }
const emptyStatus = (): ConnectionStatus => ({ state: 'disconnected', receive_ready: false, send_ready: false, error: '', history_scope: 'local' })

export default defineComponent({
  name: 'MessageInboxPage',
  props: { refreshSeq: { type: Number, default: 0 } },
  setup(props) {
    const route = useRoute()
    const router = useRouter()
    const accounts = ref<PlatformAccount[]>([])
    const platform = ref<InboxPlatform>(route.query.platform === 'xhs' ? 'xhs' : 'dy')
    const accountId = ref('')
    const conversationId = ref(0)
    const conversations = ref<Conversation[]>([])
    const messages = ref<Message[]>([])
    const status = ref<ConnectionStatus>(emptyStatus())
    const customerDetail = ref<Dict | null>(null)
    const customer = computed(() => customerDetail.value?.customer || null)
    const showCustomerDetail = ref(false)
    const detailTab = ref('sources')
    const sourcePage = ref(1)
    const eventPage = ref(1)
    const search = ref('')
    const appliedSearch = ref('')
    const error = ref('')
    const actionBusy = ref(false)
    const historyBusy = ref(false)
    const sending = ref(false)
    const deletingConversations = ref(new Set<number>())
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
    const lastActivity = new Map<string, number>()

    const refresh = createAutoSyncController({ interval: () => 3000, sync: async reason => {
      if (!active) return
      let token = revision
      try {
        if (reason === 'route' || !accounts.value.length) {
          const { data } = await api.get<PlatformAccount[]>('/accounts', { params: { feature: 'message', platform: platform.value } })
          if (!current(token)) return
          accounts.value = data.filter(item => item.platform === platform.value && item.enabled && item.features?.includes('message'))
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
          await Promise.all([loadMessages(token), loadCustomer(token)])
        } else await loadCustomer(token)
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
    function reportActivity(event: Event) {
      const account = accountId.value
      // 仅真实前台操作参与续期；轮询、收消息和程序触发事件不代表用户活动。
      if (!event.isTrusted || !active || document.hidden || platform.value !== 'dy' || !account
        || !status.value.receive_ready || !['connected', 'reconnecting'].includes(status.value.state)) return
      const now = Date.now()
      if (now - (lastActivity.get(account) ?? -Infinity) < 10000) return
      lastActivity.set(account, now)
      // 固定本次操作所属账号，异步通知失败也不阻塞编辑和发送。
      void api.post(accountPath(account) + '/activity').catch(() => {})
    }
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
      customerDetail.value = null
      showCustomerDetail.value = false
      detailTab.value = 'sources'
      sourcePage.value = eventPage.value = 1
      customerKey = ''
      error.value = ''
    }
    function navigate(account: string, conversation = 0) {
      void router.replace({ path: '/message-inbox', query: { platform: platform.value, ...(account ? { account_id: account } : {}), ...(conversation ? { conversation: String(conversation) } : {}) } })
    }
    function changePlatform(value: InboxPlatform) {
      if (value === platform.value) return
      // 立刻作废旧平台在途响应；新的账号列表返回前不展示旧聊天。
      platform.value = value
      accounts.value = []
      revision += 1
      setSelection('', 0)
      search.value = appliedSearch.value = ''
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
        // 历史补入后的本地编号可能更大，已读边界取已展示消息的最大编号。
        await api.post(`/message-inbox/conversations/${conversation}/read`, null, { params: { account_id: account, up_to_id: messages.value.length ? Math.max(...messages.value.map(item => item.id)) : undefined } })
        if (current(token)) {
          if (selected.value) selected.value.unread_count = 0
        }
      }
    }
    async function loadCustomer(token: number) {
      if (!current(token)) return
      const lead = selected.value?.lead_id
      const key = `${accountId.value}:${lead || ''}`
      // 同一客户也刷新状态；只有关联对象改变时才关闭详情并重置分页。
      if (key !== customerKey) {
        customerDetail.value = null
        showCustomerDetail.value = false
        detailTab.value = 'sources'
        sourcePage.value = eventPage.value = 1
      }
      if (!lead) { customerKey = key; return }
      const { data } = await api.get(`/message-workbench/customers/${lead}`)
      // 只保存当前会话仍关联的客户，迟到响应不能覆盖其它客户。
      if (current(token) && selected.value?.lead_id === lead) { customerDetail.value = data; customerKey = key }
    }
    async function deleteConversation(item: Conversation) {
      if (deletingConversations.value.has(item.id)) return
      const account = accountId.value
      deletingConversations.value.add(item.id)
      try {
        await api.delete(`/message-inbox/conversations/${item.id}`, { params: { account_id: account } })
        if (active && account === accountId.value) {
          // 作废删除前的列表响应，防止迟到的轮询把已隐藏会话放回列表。
          revision += 1
          conversations.value = conversations.value.filter(row => row.id !== item.id)
          if (conversationId.value === item.id) { setSelection(account, 0); navigate(account) }
          void refresh.trigger('route')
          ElMessage.success('会话已从列表删除，聊天记录已保留')
        }
      } catch (cause: any) {
        if (active && account === accountId.value) ElMessage.error(apiError(cause, '删除会话失败'))
      } finally { deletingConversations.value.delete(item.id) }
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
    async function accountAction(action: 'connect' | 'disconnect' | 'sync-auth' | 'sdk-login' | 'sdk-login/cancel' | 'sync-history') {
      if (action === 'sync-history') { await syncHistory(); return }
      if (!accountId.value || actionBusy.value) return
      actionBusy.value = true
      let token = ++revision
      const account = accountId.value
      try {
        const { data } = await api.post(accountPath(account) + '/' + action)
        if (current(token)) {
          // 让操作前启动的状态轮询失效，避免迟到结果覆盖新的二维码。
          token = ++revision
          if (action.startsWith('sdk-login')) status.value = { ...status.value, sdk_login: data as SdkLoginStatus }
          if (action === 'sync-auth') ElMessage.success('已更新私信认证状态')
          await refresh.trigger('route')
        }
      } catch (cause: any) { if (current(token)) ElMessage.error(apiError(cause, '私信操作失败')) }
      finally { actionBusy.value = false }
    }
    async function syncHistory(conversation?: number) {
      if (platform.value !== 'dy' || !accountId.value || historyBusy.value) return
      let token = revision
      const account = accountId.value
      historyBusy.value = true
      try {
        // 平台历史只由用户显式请求，三秒轮询仍只刷新本地记录。
        const { data } = conversation
          ? await api.post<{ imported: number; total: number }>(`/message-inbox/conversations/${conversation}/sync-history`, null, { params: { account_id: account } })
          : await api.post<{ imported: number; total: number }>(accountPath(account) + '/sync-history')
        if (!current(token)) return
        token = ++revision
        // 补入历史会改变第一页及分页边界，重新读取后才能继续向前翻页。
        messages.value = []
        hasMore.value = false
        ElMessage.success(`历史同步完成：拉取 ${data.total} 条，新增 ${data.imported} 条`)
        await refresh.trigger('route')
      } catch (cause: any) { if (current(token)) ElMessage.error(apiError(cause, '平台历史同步失败')) }
      finally { historyBusy.value = false }
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
          // 后台刷新不延长发送状态，避免已提交的消息仍显示发送中。
          void refresh.trigger('visible')
        }
      } catch (cause: any) { if (current(token)) { ElMessage.error(apiError(cause, '发送失败，请查看消息记录')); void refresh.trigger('visible') } }
      finally { sending.value = false }
    }

    watch(() => [route.name, route.query.account_id, route.query.conversation, route.query.draft, route.query.platform] as const, ([name, account, conversation, _draft, nextPlatform]) => {
      if (name !== 'message-inbox') return
      changePlatform(nextPlatform === 'xhs' ? 'xhs' : 'dy')
      setSelection(String(account || accountId.value), Number(conversation || 0))
      restoreRouteDraft()
      if (active) void refresh.trigger('route')
    })
    watch(() => props.refreshSeq, () => { if (active) void refresh.trigger('route') })
    function start() {
      active = true
      changePlatform(route.query.platform === 'xhs' ? 'xhs' : 'dy')
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

    return () => h('div', {
      class: 'workspace-page operations-page message-inbox-page',
      onPointerdownCapture: reportActivity, onKeydownCapture: reportActivity,
    }, [
      h(MessageInboxControls, {
        platform: platform.value, accountId: accountId.value, accounts: accounts.value,
        status: status.value, error: error.value, busy: actionBusy.value, historyBusy: historyBusy.value,
        onPlatformChange: (value: InboxPlatform) => { changePlatform(value); navigate('') },
        onAccountChange: (value: string) => navigate(value), onAction: accountAction,
        onManage: () => router.push({ path: '/accounts', query: { feature: 'message', platform: platform.value, return_to: route.fullPath } }),
      }),
      h('section', { class: 'panel inbox-layout' }, [
        h('aside', { class: 'inbox-conversations', 'aria-label': '私信会话' }, [
          h('form', { class: 'inbox-search', onSubmit: (event: Event) => { event.preventDefault(); appliedSearch.value = search.value.trim(); revision += 1; void refresh.trigger('route') } }, [
            h('input', { value: search.value, placeholder: '搜索会话', 'aria-label': '搜索会话', onInput: (event: Event) => search.value = (event.target as HTMLInputElement).value }),
            h('button', { type: 'submit' }, '搜索'),
          ]),
          h('div', { class: 'inbox-conversation-list' }, conversations.value.length ? conversations.value.map(item => h('div', {
            key: item.id, class: ['inbox-conversation-row', item.id === conversationId.value ? 'is-active' : ''],
          }, [h('button', {
            type: 'button', class: ['inbox-conversation', item.id === conversationId.value ? 'is-active' : ''], 'aria-pressed': item.id === conversationId.value,
            onClick: () => navigate(accountId.value, item.id),
          }, [avatar(item), h('div', { class: 'inbox-conversation-summary' }, [h('div', { class: 'inbox-conversation-title' }, [h('strong', { title: item.nickname || item.peer_user_id }, item.nickname || item.peer_user_id), item.unread_count ? h('span', { class: 'inbox-unread', 'aria-label': `${item.unread_count} 条未读` }, String(item.unread_count)) : null]),
            h('p', item.last_message || '暂无消息'), h('time', item.last_message_at || ''),
          ]),
          ]), h('button', {
            type: 'button', class: 'inbox-conversation-delete', 'aria-label': `删除会话：${item.nickname || item.peer_user_id}`,
            title: '从会话列表删除，保留聊天记录', disabled: deletingConversations.value.has(item.id), onClick: () => deleteConversation(item),
          }, deletingConversations.value.has(item.id) ? '删除中' : '删除')])) : h('p', { class: 'inbox-empty' }, accountId.value ? '暂无本地会话，可从客户详情打开会话。' : `请先配置${platformName(platform.value)}私信账号。`)),
        ]),
        h('div', { class: 'inbox-chat' }, selected.value ? [
          h('header', { class: 'inbox-chat-header' }, [avatar(selected.value), h('strong', { title: selected.value.nickname || selected.value.peer_user_id }, selected.value.nickname || selected.value.peer_user_id), h('span', { class: 'note' }, `${platformName(platform.value)}私信`),
            platform.value === 'dy' ? h('button', { class: 'inbox-history-action', disabled: !status.value.receive_ready || actionBusy.value || historyBusy.value || ['syncing', 'logging_in'].includes(status.value.state) || ['starting', 'qrcode', 'verification'].includes(status.value.sdk_login?.state || ''), onClick: () => syncHistory(conversationId.value) }, historyBusy.value ? '同步历史中…' : '同步此会话历史') : null,
          ]),
          h('div', { class: 'inbox-transcript', ref: transcript, role: 'log', 'aria-label': '聊天记录' }, [
            hasMore.value ? h('button', { class: 'inbox-earlier secondary-action', disabled: loadingEarlier.value, onClick: earlier }, loadingEarlier.value ? '加载中…' : '加载更早记录') : null,
            ...messages.value.map(item => h('article', { key: item.id, class: ['inbox-message', `is-${item.direction}`] }, [
              h('div', { class: 'inbox-message-content' }, item.content || '暂不支持展示此消息内容'),
              h('div', { class: 'inbox-message-meta' }, [h('time', item.sent_at || item.created_at), item.direction === 'outgoing' ? h('span', { class: `inbox-delivery is-${item.status}`, title: item.error || '' }, deliveryLabel(item.status)) : null]),
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
            h('button', { type: 'button', onClick: () => showCustomerDetail.value = true }, '查看客户详情'),
          ] : h('p', { class: 'note' }, selected.value ? '当前会话尚无客户关联。' : '选择会话后查看客户信息。'),
        ]),
      ]),
      h(ElDrawer, {
        modelValue: route.name === 'message-inbox' && showCustomerDetail.value && Boolean(customer.value),
        title: '客户详情', size: '820px', class: 'operations-detail',
        'onUpdate:modelValue': (value: boolean) => showCustomerDetail.value = value,
      }, () => renderCustomerDetail(customerDetail.value || {}, (name: string) => { if (name === 'close-detail') showCustomerDetail.value = false }, {
        detailTab: detailTab.value, setDetailTab: (value: string) => detailTab.value = value,
        sourcePage: sourcePage.value, setSourcePage: (value: number) => sourcePage.value = value,
        eventPage: eventPage.value, setEventPage: (value: number) => eventPage.value = value,
      })),
    ])
  },
})

function apiError(error: any, fallback: string) { return typeof error?.response?.data?.detail === 'string' ? error.response.data.detail : fallback }
// 按平台发送时间合并历史；时间相同时用本地编号保持稳定顺序。
function mergeMessages(previous: Message[], incoming: Message[]) { return [...new Map([...previous, ...incoming].map(item => [item.id, item])).values()].sort((left, right) => (Date.parse(left.sent_at || left.created_at) || 0) - (Date.parse(right.sent_at || right.created_at) || 0) || left.id - right.id) }
function deliveryLabel(status: string) { return ({ sending: '发送中', accepted: '已提交', failed: '发送失败', uncertain: '结果待确认', received: '已接收' } as Record<string, string>)[status] || '结果待确认' }
