import { computed, defineComponent, h, onActivated, onMounted, ref, watch } from 'vue'
import { ElDrawer, ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { Collection, CopyDocument, Promotion, Search } from '@element-plus/icons-vue'
import '../styles/operations.css'
import { platformName } from '../shared/format'
import { api } from '../shared/api'
import { isAccountFeatureReady } from '../shared/accounts'
import type { Dict, PlatformAccount } from '../shared/types'
import { COMPACT_PAGE_SIZE, ListPagination, paginateItems, type PageChange } from '../components/ui/ListPagination'
import { iconBadge, sectionTitle } from '../components/ui/Workbench'
import { copyAiScript, renderCustomerDetail } from '../components/CustomerDetail'

// “已超时”是提醒视图，不改变客户原有跟进状态。
const statusTabs = ['待私信', '已私信', '未回复', '已超时', '已回复', '未成交', '已成交', '全部']
const keywordPageSize = 8
const autoBatchConfigKey = 'message-workbench-auto-batch-config'
const autoBatchDefaults = { count: 10, min: 5, max: 20 }

export default defineComponent({
  name: 'MessageWorkbenchPage',
  props: {
    keywords: { type: Array, default: () => [] },
    customers: { type: Object, default: () => ({ rows: [], total: 0, page: 1, page_size: 20, total_pages: 1 }) },
    detail: { type: Object, default: () => ({}) },
    filters: { type: Object, default: () => ({ keyword: '', status: '待私信', intention: '', query: '', page: 1, page_size: 20 }) },
    batches: { type: Object, default: () => ({ batches: [], active: null, items: [] }) },
    settings: { type: Object, default: () => ({}) },
    loading: { type: Boolean, default: false },
  },
  emits: ['filter-change', 'select-customer', 'message-customer', 'confirm-message-customer', 'auto-message-customer', 'start-auto-message-batch', 'cancel-auto-message-batch', 'update-follow-status', 'close-detail'],
  setup(props, { emit }) {
    const route = useRoute()
    const router = useRouter()
    const detailTab = ref('sources')
    function handleEvent(name: any, ...args: any[]) {
      emit(name, ...args)
      if (name === 'select-customer') void router.replace({ query: { ...route.query, customer: String(args[0]) } })
      if (name === 'close-detail') { const { customer, ...query } = route.query; void router.replace({ query }) }
    }
    watch(() => [route.name, route.query.customer] as const, ([name, value]) => {
      // 缓存页只读取自己的详情参数，避免其它页签触发客户请求或关闭详情。
      if (name !== 'message-workbench') return
      if (value && String((props.detail as Dict)?.customer?.lead_id || '') !== String(value)) emit('select-customer', String(value))
      else if (!value && (props.detail as Dict)?.customer) emit('close-detail')
    }, { immediate: true })
    watch(() => [route.name, route.query] as const, ([name, value]) => {
      if (name !== 'message-workbench') return
      const next = { keyword: String(value.keyword || ''), platform: String(value.platform || ''), status: String(value.status || '待私信'), intention: String(value.intention || ''), query: String(value.query || ''), page: Number(value.page || 1), page_size: Number(value.page_size || 20) }
      if (Object.entries(next).some(([key, item]) => String(props.filters[key] ?? '') !== String(item))) emit('filter-change', next)
    }, { immediate: true })
    const savedBatchConfig = loadAutoBatchConfig()
    const queryDraft = ref(String((props.filters as Dict).query || ''))
    const keywordPages = ref<Record<string, number>>({})
    const batchCount = ref(savedBatchConfig.count)
    const intervalMin = ref(savedBatchConfig.min)
    const intervalMax = ref(savedBatchConfig.max)
    const accounts = ref<PlatformAccount[]>([])
    const accountId = ref('')
    const openingConversation = ref(false)
    const sourcePage = ref(1)
    const eventPage = ref(1)
    async function loadAccounts() {
      try {
        const { data } = await api.get<PlatformAccount[]>('/accounts', { params: { feature: 'message' } })
        accounts.value = data.filter(account => ['dy', 'xhs'].includes(account.platform) && isAccountFeatureReady(account, 'message'))
        selectBatchAccount()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '私信账号加载失败')
      }
    }
    onMounted(loadAccounts)
    // 来源平台切换时，只保留或选择同平台的私信账号。
    watch(() => String((props.filters as Dict).platform || ''), selectBatchAccount)
    // 返回缓存页时更新账号可用状态，同时保留用户已选账号。
    let activatedOnce = false
    onActivated(() => { if (activatedOnce) void loadAccounts(); activatedOnce = true })
    watch(() => (props.filters as Dict).query, value => {
      queryDraft.value = String(value || '')
    })
    watch(() => props.keywords, () => {
      keywordPages.value = {}
    })
    watch(() => String((props.detail as Dict)?.customer?.lead_id || ''), () => {
      sourcePage.value = 1
      eventPage.value = 1
    })
    watch([batchCount, intervalMin, intervalMax], () => {
      saveAutoBatchConfig({ count: batchCount.value, min: intervalMin.value, max: intervalMax.value })
    })

    const rows = computed(() => (props.customers as Dict).rows || [])
    const total = computed(() => Number((props.customers as Dict).total || 0))
    const page = computed(() => Number((props.customers as Dict).page || 1))
    const pageSize = computed(() => Number((props.customers as Dict).page_size || 20))
    const allKeyword = computed(() => (props.keywords as Dict[]).find(item => !String(item.platform || '') && !String(item.keyword || '')))
    const keywordGroups = computed(() => {
      const groups: Record<string, Dict[]> = {}
      for (const item of props.keywords as Dict[]) {
        const platform = String(item.platform || '')
        if (!platform) continue
        if (!groups[platform]) groups[platform] = []
        groups[platform].push(item)
      }
      return Object.entries(groups)
        .sort(([left], [right]) => platformSort(left) - platformSort(right) || platformName(left).localeCompare(platformName(right)))
        .map(([platform, items]) => ({ platform, items }))
    })

    function changeFilter(next: Dict) {
      const values = { ...(props.filters as Dict), ...next }
      emit('close-detail')
      emit('filter-change', values)
      void router.replace({ path: '/message-workbench', query: { ...(route.query.return_to ? { return_to: route.query.return_to } : {}), keyword: String(values.keyword || ''), platform: String(values.platform || ''), status: String(values.status || '待私信'), intention: String(values.intention || ''), query: String(values.query || ''), page: Number(values.page || 1), page_size: Number(values.page_size || 20) } })
    }

    function runSearch() {
      changeFilter({ query: queryDraft.value.trim(), page: 1 })
    }

    function accountForPlatform(platform: string) {
      const matching = accounts.value.filter(account => account.platform === platform)
      return String(matching.find(account => account.id === accountId.value)?.id
        || matching.find(account => account.default_features?.includes('message'))?.id || matching[0]?.id || '')
    }

    function selectBatchAccount() {
      accountId.value = accountForPlatform(String((props.filters as Dict).platform || ''))
    }

    // 只创建本地会话并打开收件箱，不在此处发送消息或启动监听。
    async function openConversation(customer: Dict) {
      const account = accountForPlatform(String(customer.platform))
      if (!account || openingConversation.value || !['dy', 'xhs'].includes(customer.platform)) return
      openingConversation.value = true
      try {
        const { data } = await api.post('/message-inbox/conversations', { account_id: account, lead_id: customer.lead_id })
        await router.push({ path: '/message-inbox', query: { platform: customer.platform, account_id: account, conversation: String(data.id) } })
      } catch (error: any) { ElMessage.error(error?.response?.data?.detail || '会话打开失败') }
      finally { openingConversation.value = false }
    }

    return () => h('div', { class: 'workspace-page operations-page' }, [
      h('section', { class: 'panel' }, [
        sectionTitle({ title: '客户跟进', subtitle: (props.filters as Dict).keyword ? `${platformName((props.filters as Dict).platform)} / ${(props.filters as Dict).keyword}` : '全部来源 · 准备话术，实际发送后确认，再推进回复与成交状态。', icon: Collection }),
        h('div', { class: 'toolbar' }, [
          String(route.query.return_to || '').startsWith('/message-batches?') ? h('button', { onClick: () => router.push(String(route.query.return_to)) }, '返回原批次') : null,
          h('button', { onClick: () => router.push({ path: '/accounts', query: { feature: 'message', platform: props.filters.platform === 'xhs' ? 'xhs' : 'dy', return_to: route.fullPath } }) }, '配置私信账号'),
        ]),
        h('details', [h('summary', '按平台与来源关键词筛选'), h('div', { class: 'message-keyword-list' }, [
          allKeyword.value ? renderKeywordButton(allKeyword.value, props.filters as Dict, changeFilter) : null,
          ...keywordGroups.value.map(group => renderKeywordGroup(group, props.filters as Dict, changeFilter, keywordPages.value[group.platform] || 1, page => keywordPages.value = { ...keywordPages.value, [group.platform]: page })),
        ])]),
      ]),
      h('section', { class: 'panel message-workbench' }, [
        (props.batches as Dict).active ? h('div', { class: 'toolbar' }, [h('span', { class: 'note' }, '有自动私信批次正在排队或执行'), h('button', { onClick: () => router.push({ path: '/message-batches', query: { batch: String((props.batches as Dict).active.id) } }) }, '查看执行结果')]) : null,
        h('details', { class: 'operations-advanced' }, [h('summary', '批量自动私信'),

        renderAutoBatchControls(props.filters as Dict, props.batches as Dict, batchCount.value, intervalMin.value, intervalMax.value, {
          setCount: (value: number) => batchCount.value = boundedNumber(value, autoBatchDefaults.count, 1, 200),
          setMin: (value: number) => intervalMin.value = boundedNumber(value, autoBatchDefaults.min, 0, 3600),
          setMax: (value: number) => intervalMax.value = boundedNumber(value, autoBatchDefaults.max, 0, 3600),
          accountId: accountId.value,
          accounts: accounts.value.filter(account => account.platform === (props.filters as Dict).platform),
          setAccountId: (value: string) => { accountId.value = value },
          start: () => emit('start-auto-message-batch', {
            platform: (props.filters as Dict).platform || '',
            keyword: (props.filters as Dict).keyword || '',
            count: batchCount.value,
            interval_min_seconds: intervalMin.value,
            interval_max_seconds: intervalMax.value,
            account_id: accountId.value
          }),
          cancel: (batch: Dict) => emit('cancel-auto-message-batch', batch),
        })]),
        h('div', { class: 'message-status-tabs' }, [
          h('select', { value: String(props.filters.intention || ''), 'aria-label': '客户意向', onChange: (event: Event) => changeFilter({ intention: (event.target as HTMLSelectElement).value, page: 1 }) }, [h('option', { value: '' }, '全部意向'), ...['高', '中', '低'].map(value => h('option', { value }, `${value}意向`))]),
          ...statusTabs.map(status => h('button', {
          type: 'button',
          class: String((props.filters as Dict).status || '待私信') === status ? 'active' : '',
          onClick: () => changeFilter({ status, page: 1 })
          }, status)),
          h('div', { class: 'message-search' }, [
            h('input', {
              value: queryDraft.value,
              placeholder: '搜索客户/评论/来源；逗号分隔任一匹配',
              onInput: (event: Event) => queryDraft.value = (event.target as HTMLInputElement).value,
              onKeydown: (event: KeyboardEvent) => {
                if (event.key === 'Enter') runSearch()
              }
            }),
            h('button', { type: 'button', onClick: runSearch }, [h(Search), h('span', '搜索')])
          ])
        ]),
        renderCustomerTable(rows.value, props.loading, handleEvent, props.settings as Dict, accountForPlatform),
        h(ListPagination, {
          page: page.value,
          pageSize: pageSize.value,
          total: total.value,
          onChange: (payload: PageChange) => changeFilter({ page: payload.page, page_size: payload.page_size }),
        }),
        h(ElDrawer, { modelValue: route.name === 'message-workbench' && Boolean((props.detail as Dict)?.customer), title: '客户详情', size: '820px', class: 'operations-detail', 'onUpdate:modelValue': (value: boolean) => { if (!value && route.name === 'message-workbench') handleEvent('close-detail') } }, () => renderCustomerDetail(props.detail as Dict, handleEvent, {
          detailTab: detailTab.value, setDetailTab: (value: string) => detailTab.value = value,
          sourcePage: sourcePage.value,
          eventPage: eventPage.value,
          setSourcePage: (value: number) => { sourcePage.value = value },
          setEventPage: (value: number) => { eventPage.value = value },
          accountId: accountForPlatform(String((props.detail as Dict)?.customer?.platform || 'dy')),
          openingConversation: openingConversation.value,
          openConversation,
        }))
      ])
    ])
  }
})

function renderKeywordButton(keyword: Dict, filters: Dict, changeFilter: (next: Dict) => void) {
  const selected = String(filters.keyword || '') === String(keyword.keyword || '') && String(filters.platform || '') === String(keyword.platform || '')
  return h('button', {
    type: 'button',
    class: ['message-keyword-item', selected ? 'active' : ''],
    onClick: () => changeFilter({ keyword: keyword.keyword || '', platform: keyword.platform || '', page: 1 })
  }, [
    h('div', { class: 'message-keyword-main' }, [
      iconBadge(Promotion, selected ? 'teal' : 'gray'),
      h('div', [
        h('strong', keyword.label || keyword.keyword || '全部'),
        h('small', `${keyword.customer_count || 0} 个客户`)
      ])
    ]),
    h('div', { class: 'keyword-metrics' }, [
      h('span', `待私信 ${keyword.unmessaged_count || 0}`),
      h('span', `未回复 ${keyword.waiting_reply_count || 0}`),
      keyword.overdue_count ? h('span', { class: 'warn' }, `超时 ${keyword.overdue_count}`) : null
    ])
  ])
}

function renderKeywordGroup(
  group: { platform: string, items: Dict[] },
  filters: Dict,
  changeFilter: (next: Dict) => void,
  page: number,
  setPage: (page: number) => void
) {
  const pagination = paginateItems(group.items, page, keywordPageSize)
  return h('details', { class: 'message-platform-group', open: true }, [
    h('summary', [
      h('strong', platformName(group.platform)),
      h('span', `${group.items.length} 个关键词`)
    ]),
    h('div', { class: 'message-platform-keywords' }, pagination.items.map(keyword => renderKeywordButton(keyword, filters, changeFilter))),
    h(ListPagination, {
      page: pagination.page,
      pageSize: pagination.pageSize,
      total: pagination.total,
      compact: true,
      onChange: (payload: PageChange) => setPage(payload.page),
    }),
  ])
}

function platformSort(platform: string) {
  return ({ dy: 1, xhs: 2, ks: 3 } as Record<string, number>)[platform] || 9
}

function renderAutoBatchControls(filters: Dict, batches: Dict, count: number, minSeconds: number, maxSeconds: number, actions: Dict) {
  const keyword = String(filters.keyword || '')
  const platform = String(filters.platform || '')
  const active = batches.active
  const unsupportedPlatform = !['dy', 'xhs'].includes(platform)
  const disabled = Boolean(active) || unsupportedPlatform || !keyword || !String(actions.accountId || '')
  return h('section', { class: 'auto-message-batch-panel' }, [
    h('div', { class: 'auto-message-batch-title' }, [
      h('div', { class: 'auto-message-batch-heading' }, [
        h('strong', 'AI一键私信'),
        h('span', keyword ? `${platformName(platform)} / ${keyword}` : '先选择一个抖音或小红书来源关键词'),
      ]),
      h('label', { class: 'auto-message-account' }, [
        h('span', '执行账号'),
        h('select', {
          value: actions.accountId,
          onChange: (event: Event) => actions.setAccountId((event.target as HTMLSelectElement).value),
        }, [
          h('option', { value: '' }, actions.accounts.length ? '请选择账号' : '账号中心暂无当前平台可用账号'),
          ...actions.accounts.map((account: PlatformAccount) => h('option', { value: account.id }, account.name)),
        ]),
      ]),
    ]),
    h('div', { class: 'auto-message-batch-form' }, [
      h('label', ['私信数量', h('input', {
        type: 'number',
        min: '1',
        max: '200',
        value: count,
        onInput: (event: Event) => actions.setCount(Number((event.target as HTMLInputElement).value || 1))
      })]),
      h('label', ['间隔最小秒', h('input', {
        type: 'number',
        min: '0',
        max: '3600',
        value: minSeconds,
        onInput: (event: Event) => actions.setMin(Number((event.target as HTMLInputElement).value || 0))
      })]),
      h('label', ['间隔最大秒', h('input', {
        type: 'number',
        min: '0',
        max: '3600',
        value: maxSeconds,
        onInput: (event: Event) => actions.setMax(Number((event.target as HTMLInputElement).value || 0))
      })]),
      h('button', {
        type: 'button',
        class: 'primary-action compact-action',
        disabled,
        title: active ? '已有自动私信批次正在执行' : unsupportedPlatform ? '请选择抖音或小红书来源关键词' : !keyword ? '请先选择具体关键词' : '启动批量自动私信',
        onClick: actions.start
      }, [h(Promotion), h('span', active ? '运行中' : 'AI一键私信')]),
      active ? h('button', { type: 'button', class: 'secondary-action compact-action', onClick: () => actions.cancel(active) }, '取消批次') : null,
      h('p', { class: 'auto-message-platform-note' }, '支持抖音、小红书，使用来源平台对应的私信账号。')
    ]),
    // 批次按来源选人，列表筛选和分页不会缩小自动发送范围。
    h('p', { class: 'note' }, `批量范围：所选来源关键词下全部未私信、话术可用的目标客户，按评论/来源时间倒序选取最多 ${count} 人；当前列表的意向、文字搜索、状态和分页不参与批次筛选。`)
  ])
}

function renderCustomerTable(rows: Dict[], loading: boolean, emit: any, settings: Dict, accountForPlatform: (platform: string) => string) {
  const body = loading
    ? [h('tr', [h('td', { colspan: 6, class: 'message-empty' }, '加载中...')])]
    : rows.length
      ? rows.map(row => renderCustomerRow(row, emit, settings, accountForPlatform(String(row.platform))))
      : [h('tr', [h('td', { colspan: 6, class: 'message-empty' }, '当前筛选下暂无客户')])]

  return h('div', { class: 'message-table-wrap' }, [
    h('table', { class: 'message-table' }, [
      h('thead', [
        h('tr', [
          h('th', '客户'),
          h('th', '评论内容'),
          h('th', '来源视频'),
          h('th', 'AI话术'),
          h('th', '时间'),
          h('th', { class: 'message-sticky-action' }, '操作/状态')
        ])
      ]),
      h('tbody', body)
    ])
  ])
}

function renderCustomerRow(row: Dict, emit: any, settings: Dict, accountId: string) {
  const rawScript = String(row.script || '').trim()
  const script = rawScript || '暂无AI话术'
  const sendScript = selectedMessageScript(row, settings)
  const missingScriptTip = scriptMode(settings) === 'fixed' ? '固定话术为空，请先到“私信设置”填写' : '暂无AI话术'
  const autoDmUnsupported = !['dy', 'xhs'].includes(row.platform)
  return h('tr', { class: row.overdue ? 'is-overdue' : '', onClick: () => emit('select-customer', row.lead_id) }, [
    h('td', { class: 'message-customer-cell' }, [
      row.profile_url
        ? h('a', { href: row.profile_url, target: '_blank', rel: 'noreferrer', onClick: (event: Event) => event.stopPropagation() }, row.nickname || '-')
        : h('strong', row.nickname || '-'),
      h('small', `${platformName(row.platform)} · ${row.keyword_text || '未标记关键词'}`),
      row.source_account_name ? h('small', `来源：${row.source_account_name}`) : null
    ]),
    h('td', { class: 'message-rich-cell' }, [renderClamp(row.comment_text || '-', 5)]),
    h('td', { class: 'message-video-cell' }, [
      h('div', { class: 'message-video-summary' }, [renderClamp(row.video_text || '-', 4)]),
      row.content_url
        ? h('a', { class: 'message-link', href: row.content_url, target: '_blank', rel: 'noreferrer', onClick: (event: Event) => event.stopPropagation() }, '打开视频')
        : null
    ]),
    h('td', {
      class: ['message-rich-cell', 'message-script-cell', rawScript ? 'copyable-script' : 'empty-script'],
      title: rawScript ? `${rawScript}\n点击复制AI话术` : script,
      role: rawScript ? 'button' : undefined,
      tabindex: rawScript ? 0 : undefined,
      onClick: rawScript ? (event: MouseEvent) => copyAiScript(rawScript, event) : undefined,
      onKeydown: rawScript ? (event: KeyboardEvent) => {
        if (event.key === 'Enter' || event.key === ' ') copyAiScript(rawScript, event)
      } : undefined
    }, [renderClamp(script, 5)]),
    h('td', { class: 'message-time-cell' }, [
      h('span', `评论 ${row.comment_at || '-'}`),
      h('span', `私信 ${row.private_message_at || '-'}`),
      h('span', `回复 ${row.reply_at || '-'}`)
    ]),
    h('td', { class: 'message-action-status-cell message-sticky-action', onClick: (event: Event) => event.stopPropagation() }, [
      h('div', { class: 'message-action-status-stack' }, [
        h('div', { class: 'message-action-row' }, [
          h('button', {
            type: 'button',
            class: ['text-icon-button', autoDmUnsupported ? 'is-platform-disabled' : ''],
            disabled: autoDmUnsupported || !sendScript || !accountId,
            title: autoDmUnsupported ? '自动私信支持抖音和小红书客户' : !sendScript ? missingScriptTip : !accountId ? '请先在账号中心配置私信账号' : settings.auto_dm_fill_only ? '在收件箱生成草稿，由你手动发送' : '通过私信接口发送话术',
            onClick: () => emit('auto-message-customer', { ...row, account_id: accountId })
          }, [h(Promotion), h('span', '自动私信')]),
          h('button', {
            type: 'button',
            class: 'text-icon-button',
            disabled: !sendScript || (['dy', 'xhs'].includes(row.platform) ? !accountId : !row.profile_url),
            title: !sendScript ? missingScriptTip : ['dy', 'xhs'].includes(row.platform) ? '在私信收件箱预填话术，不发送消息' : '复制话术并打开主页，不修改跟进状态',
            onClick: () => emit('message-customer', ['dy', 'xhs'].includes(row.platform) ? { ...row, account_id: accountId } : row)
          }, [h(CopyDocument), h('span', '准备私信')]),
          ['待筛选', '未私信'].includes(String(row.follow_status)) ? h('button', { type: 'button', class: 'text-icon-button', onClick: () => emit('confirm-message-customer', row) }, '确认已发送') : null
        ]),
        h('div', { class: 'message-action-row' }, [
          h('select', {
            class: ['follow-select', followStatusClass(row.follow_status)],
            value: row.follow_status || '未私信',
            onChange: (event: Event) => emit('update-follow-status', row, (event.target as HTMLSelectElement).value)
          }, followOptions(row.follow_status).map(status => h('option', { value: status }, status))),
          h('button', { type: 'button', class: 'text-icon-button compact', onClick: () => emit('select-customer', row.lead_id) }, '详情')
        ]),
        row.overdue ? h('span', { class: 'overdue-badge' }, `超时 ${row.overdue_days || 0} 天`) : null
      ])
    ])
  ])
}

function selectedMessageScript(row: Dict, settings: Dict) {
  return scriptMode(settings) === 'fixed'
    ? String(settings.fixed_dm_script || '').trim()
    : String(row.script || '').trim()
}

function scriptMode(settings: Dict) {
  return String(settings.dm_script_mode || 'ai') === 'fixed' ? 'fixed' : 'ai'
}

function loadAutoBatchConfig() {
  try {
    const raw = window.localStorage.getItem(autoBatchConfigKey)
    const data = raw ? JSON.parse(raw) : {}
    return {
      count: boundedNumber(data.count, autoBatchDefaults.count, 1, 200),
      min: boundedNumber(data.min, autoBatchDefaults.min, 0, 3600),
      max: boundedNumber(data.max, autoBatchDefaults.max, 0, 3600)
    }
  } catch {
    return autoBatchDefaults
  }
}

function saveAutoBatchConfig(config: { count: number, min: number, max: number }) {
  try {
    window.localStorage.setItem(autoBatchConfigKey, JSON.stringify(config))
  } catch {
    // ponytail: localStorage may be blocked; defaults still keep the page usable.
  }
}

function boundedNumber(value: unknown, fallback: number, min: number, max: number) {
  const number = Number(value)
  return Number.isFinite(number) ? Math.min(max, Math.max(min, Math.trunc(number))) : fallback
}


function renderClamp(value: unknown, lines: number) {
  const text = String(value || '')
  return h('span', { class: `message-clamp clamp-${lines}`, title: text }, text)
}


function followOptions(current: string) {
  const status = current || '未私信'
  const map: Record<string, string[]> = {
    '未私信': ['未私信', '已私信', '未回复', '非客户', '待筛选'],
    '已私信': ['已私信', '未回复', '已回复', '未成交', '未私信', '非客户', '待筛选'],
    '未回复': ['未回复', '已回复', '未成交', '已私信', '未私信', '非客户', '待筛选'],
    '已回复': ['已回复', '已成交', '未成交', '未回复', '未私信', '非客户', '待筛选'],
    '未成交': ['未成交', '已成交', '已回复', '未回复', '未私信', '非客户', '待筛选'],
    '已成交': ['已成交', '已回复', '未成交', '未回复', '未私信', '非客户', '待筛选'],
  }
  return map[status] || [status, '未私信', '非客户', '待筛选']
}

function followStatusClass(status: string) {
  if (status === '未私信') return 'is-unmessaged'
  if (['已私信', '未回复'].includes(status)) return 'is-waiting'
  if (status === '已回复') return 'is-replied'
  if (status === '未成交') return 'is-lost'
  if (status === '已成交') return 'is-won'
  return 'is-unknown'
}
