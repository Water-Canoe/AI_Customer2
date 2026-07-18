import { computed, defineComponent, h, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Close, Collection, CopyDocument, Promotion, Search } from '@element-plus/icons-vue'
import { SplitPane } from '../components/ui/SplitPane'
import { platformName } from '../shared/format'
import { api } from '../shared/api'
import { isAccountFeatureReady } from '../shared/accounts'
import type { Dict } from '../shared/types'
import { iconBadge, sectionTitle } from '../components/ui/Workbench'

// “已超时”是提醒视图，不改变客户原有跟进状态。
const statusTabs = ['待私信', '已私信', '未回复', '已超时', '已回复', '未成交', '已成交', '全部']
const keywordPageSize = 8
const autoBatchConfigKey = 'message-workbench-auto-batch-config'
const autoBatchDefaults = { count: 10, min: 5, max: 20 }

export default defineComponent({
  props: {
    keywords: { type: Array, default: () => [] },
    customers: { type: Object, default: () => ({ rows: [], total: 0, page: 1, page_size: 20, total_pages: 1 }) },
    detail: { type: Object, default: () => ({}) },
    filters: { type: Object, default: () => ({ keyword: '', status: '待私信', query: '', page: 1, page_size: 20 }) },
    batches: { type: Object, default: () => ({ batches: [], active: null, items: [] }) },
    settings: { type: Object, default: () => ({}) },
    loading: { type: Boolean, default: false },
  },
  emits: ['filter-change', 'select-customer', 'message-customer', 'auto-message-customer', 'start-auto-message-batch', 'cancel-auto-message-batch', 'update-follow-status', 'close-detail'],
  setup(props, { emit }) {
    const savedBatchConfig = loadAutoBatchConfig()
    const queryDraft = ref(String((props.filters as Dict).query || ''))
    const keywordPages = ref<Record<string, number>>({})
    const batchCount = ref(savedBatchConfig.count)
    const intervalMin = ref(savedBatchConfig.min)
    const intervalMax = ref(savedBatchConfig.max)
    const accounts = ref<Dict[]>([])
    const accountId = ref('')
    async function loadAccounts() {
      try {
        const { data } = await api.get('/accounts', { params: { feature: 'message', platform: 'dy' } })
        accounts.value = data.filter((account: Dict) => isAccountFeatureReady(account, 'message'))
        const preferred = accounts.value.find(account => account.default_features?.includes('message')) || accounts.value[0]
        accountId.value = String(preferred?.id || '')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '私信账号加载失败')
      }
    }
    onMounted(loadAccounts)
    watch(() => (props.filters as Dict).query, value => {
      queryDraft.value = String(value || '')
    })
    watch(() => props.keywords, () => {
      keywordPages.value = {}
    })
    watch([batchCount, intervalMin, intervalMax], () => {
      saveAutoBatchConfig({ count: batchCount.value, min: intervalMin.value, max: intervalMax.value })
    })

    const rows = computed(() => (props.customers as Dict).rows || [])
    const total = computed(() => Number((props.customers as Dict).total || 0))
    const page = computed(() => Number((props.customers as Dict).page || 1))
    const pageSize = computed(() => Number((props.customers as Dict).page_size || 20))
    const totalPages = computed(() => Number((props.customers as Dict).total_pages || 1))
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
      emit('filter-change', { ...(props.filters as Dict), ...next })
    }

    function runSearch() {
      changeFilter({ query: queryDraft.value.trim(), page: 1 })
    }

    return () => h(SplitPane, { storageKey: 'message-workbench', side: 'left', defaultSideWidth: 300, minSideWidth: 260, maxSideWidth: 420 }, {
      side: () => h('aside', { class: 'pane message-keyword-pane' }, [
        sectionTitle({ title: '关键词队列', subtitle: '按需求产品筛选', icon: Collection, tone: 'amber' }),
        h('div', { class: 'message-keyword-list' }, [
          allKeyword.value ? renderKeywordButton(allKeyword.value, props.filters as Dict, changeFilter) : null,
          ...keywordGroups.value.map(group => renderKeywordGroup(
            group,
            props.filters as Dict,
            changeFilter,
            keywordPages.value[group.platform] || 1,
            (page) => keywordPages.value = { ...keywordPages.value, [group.platform]: page }
          ))
        ])
      ]),
      default: () => h('section', { class: 'pane message-workbench' }, [
        h('div', { class: 'workbench-account-bar' }, [
          h('strong', '私信执行账号'),
          h('select', { value: accountId.value, onChange: (event: Event) => accountId.value = (event.target as HTMLSelectElement).value }, [
            h('option', { value: '' }, accounts.value.length ? '请选择账号' : '账号中心暂无可用抖音账号'),
            ...accounts.value.map(account => h('option', { value: account.id }, account.name))
          ])
        ]),
        renderAutoBatchControls(props.filters as Dict, props.batches as Dict, batchCount.value, intervalMin.value, intervalMax.value, {
          setCount: (value: number) => batchCount.value = boundedNumber(value, autoBatchDefaults.count, 1, 200),
          setMin: (value: number) => intervalMin.value = boundedNumber(value, autoBatchDefaults.min, 0, 3600),
          setMax: (value: number) => intervalMax.value = boundedNumber(value, autoBatchDefaults.max, 0, 3600),
          accountId: accountId.value,
          start: () => emit('start-auto-message-batch', {
            platform: (props.filters as Dict).platform || '',
            keyword: (props.filters as Dict).keyword || '',
            count: batchCount.value,
            interval_min_seconds: intervalMin.value,
            interval_max_seconds: intervalMax.value,
            account_id: accountId.value
          }),
          cancel: (batch: Dict) => emit('cancel-auto-message-batch', batch)
        }),
        h('div', { class: 'message-status-tabs' }, [
          ...statusTabs.map(status => h('button', {
          type: 'button',
          class: String((props.filters as Dict).status || '待私信') === status ? 'active' : '',
          onClick: () => changeFilter({ status, page: 1 })
          }, status)),
          h('div', { class: 'message-search' }, [
            h('input', {
              value: queryDraft.value,
              placeholder: '搜索客户/评论',
              onInput: (event: Event) => queryDraft.value = (event.target as HTMLInputElement).value,
              onKeydown: (event: KeyboardEvent) => {
                if (event.key === 'Enter') runSearch()
              }
            }),
            h('button', { type: 'button', onClick: runSearch }, [h(Search), h('span', '搜索')])
          ])
        ]),
        renderCustomerTable(rows.value, props.loading, emit, props.settings as Dict, accountId.value),
        h('div', { class: 'message-pagination' }, [
          h('span', `共 ${total.value} 个客户`),
          h('div', [
            h('button', {
              type: 'button',
              disabled: page.value <= 1,
              onClick: () => changeFilter({ page: page.value - 1 })
            }, '上一页'),
            h('strong', `${page.value} / ${totalPages.value}`),
            h('button', {
              type: 'button',
              disabled: page.value >= totalPages.value,
              onClick: () => changeFilter({ page: page.value + 1 })
            }, '下一页')
          ])
        ]),
        renderDetailDrawer(props.detail as Dict, emit)
      ])
    })
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
  const totalPages = Math.max(1, Math.ceil(group.items.length / keywordPageSize))
  const normalizedPage = Math.min(Math.max(1, page), totalPages)
  const start = (normalizedPage - 1) * keywordPageSize
  const items = group.items.slice(start, start + keywordPageSize)
  return h('details', { class: 'message-platform-group', open: true }, [
    h('summary', [
      h('strong', platformName(group.platform)),
      h('span', `${group.items.length} 个关键词`)
    ]),
    h('div', { class: 'message-platform-keywords' }, items.map(keyword => renderKeywordButton(keyword, filters, changeFilter))),
    h('div', { class: 'keyword-pagination compact' }, [
      h('span', `${normalizedPage} / ${totalPages}`),
      h('div', [
        h('button', {
          type: 'button',
          disabled: normalizedPage <= 1,
          onClick: () => setPage(normalizedPage - 1)
        }, '上一页'),
        h('strong', `${group.items.length}`),
        h('button', {
          type: 'button',
          disabled: normalizedPage >= totalPages,
          onClick: () => setPage(normalizedPage + 1)
        }, '下一页')
      ])
    ])
  ])
}

function platformSort(platform: string) {
  return ({ dy: 1, xhs: 2, ks: 3 } as Record<string, number>)[platform] || 9
}

function renderAutoBatchControls(filters: Dict, batches: Dict, count: number, minSeconds: number, maxSeconds: number, actions: Dict) {
  const keyword = String(filters.keyword || '')
  const platform = String(filters.platform || '')
  const active = batches.active
  const unsupportedPlatform = Boolean(platform) && platform !== 'dy'
  const disabled = Boolean(active) || unsupportedPlatform || !keyword || !String(actions.accountId || '')
  return h('section', { class: 'auto-message-batch-panel' }, [
    h('div', { class: 'auto-message-batch-title' }, [
      h('strong', 'AI一键私信'),
      h('span', keyword ? `${platformName(platform)} / ${keyword}` : '先在左侧选择一个抖音关键词')
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
        title: active ? '已有自动私信批次正在执行' : unsupportedPlatform ? 'AI一键私信会跳过快手/小红书平台' : !keyword ? '请先选择具体关键词' : '启动批量自动私信',
        onClick: actions.start
      }, [h(Promotion), h('span', active ? '运行中' : 'AI一键私信')]),
      active ? h('button', { type: 'button', class: 'secondary-action compact-action', onClick: () => actions.cancel(active) }, '取消批次') : null,
      h('p', { class: 'auto-message-platform-note' }, '由于快手网页版、小红书网页版不提供私信入口，因此快手与小红书两个平台私信功能不可用。请以抖音为主。后续平台优化规则，本系统会一并优化添加私信功能。')
    ])
  ])
}

function renderCustomerTable(rows: Dict[], loading: boolean, emit: any, settings: Dict, accountId: string) {
  const body = loading
    ? [h('tr', [h('td', { colspan: 6, class: 'message-empty' }, '加载中...')])]
    : rows.length
      ? rows.map(row => renderCustomerRow(row, emit, settings, accountId))
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
  const autoDmUnsupported = row.platform !== 'dy'
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
            disabled: autoDmUnsupported || !sendScript || !row.profile_url || !accountId,
            title: autoDmUnsupported ? '自动私信当前只支持抖音客户' : !sendScript ? missingScriptTip : !row.profile_url ? '缺少客户主页' : !accountId ? '请先在账号中心配置私信账号' : '自动打开抖音主页并处理话术',
            onClick: () => emit('auto-message-customer', { ...row, account_id: accountId })
          }, [h(Promotion), h('span', '自动私信')]),
          h('button', {
            type: 'button',
            class: 'text-icon-button',
            disabled: !sendScript || !row.profile_url,
            title: !sendScript ? missingScriptTip : !row.profile_url ? '缺少客户主页' : '复制话术、打开主页并标记已私信',
            onClick: () => emit('message-customer', row)
          }, [h(CopyDocument), h('span', '私信')])
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

function renderDetailDrawer(detail: Dict, emit: any) {
  if (!detail?.customer) return null
  const customer = detail.customer || {}
  const sources = detail.sources || []
  const events = detail.events || []
  const rawScript = String(customer.script || '').trim()
  const script = rawScript || '暂无AI话术'
  return h('div', { class: 'message-detail-drawer' }, [
    h('div', { class: 'drawer-head' }, [
      h('div', [
        h('h3', customer.nickname || '客户详情'),
        h('p', customer.keyword_text || '无关键词')
      ]),
      h('button', { type: 'button', class: 'icon-button', title: '关闭详情', 'aria-label': '关闭详情', onClick: () => emit('close-detail') }, [h(Close)])
    ]),
    h('div', {
      class: ['drawer-section', 'drawer-script-section', rawScript ? 'copyable-script' : 'empty-script'],
      title: rawScript ? `${rawScript}\n点击复制AI话术` : script,
      role: rawScript ? 'button' : undefined,
      tabindex: rawScript ? 0 : undefined,
      onClick: rawScript ? (event: MouseEvent) => copyAiScript(rawScript, event) : undefined,
      onKeydown: rawScript ? (event: KeyboardEvent) => {
        if (event.key === 'Enter' || event.key === ' ') copyAiScript(rawScript, event)
      } : undefined
    }, [
      h('h4', rawScript ? 'AI话术 · 点击复制' : 'AI话术'),
      h('p', script)
    ]),
    h('div', { class: 'drawer-section' }, [
      h('h4', 'AI分析原因'),
      h('p', customer.reason || '暂无AI分析原因')
    ]),
    h('div', { class: 'drawer-section' }, [
      h('h4', '来源证据'),
      ...sources.map((source: Dict) => h('article', { class: 'source-card' }, [
        h('strong', source.keyword || '未标记关键词'),
        h('p', source.comment_text || '无评论内容'),
        h('small', source.video_text || '无视频详情'),
        source.content_url ? h('a', { href: source.content_url, target: '_blank', rel: 'noreferrer' }, '打开视频') : null,
        source.source_account_name ? h('span', `来源账号：${source.source_account_name}`) : null
      ]))
    ]),
    h('div', { class: 'drawer-section' }, [
      h('h4', '跟进时间线'),
      events.length
        ? h('ol', { class: 'event-timeline' }, events.map((event: Dict) => h('li', [
            h('strong', `${event.from_status || '-'} -> ${event.to_status || '-'}`),
            h('span', event.created_at || ''),
            event.note ? h('p', event.note) : null
          ])))
        : h('p', '暂无人工跟进事件')
    ])
  ])
}

function renderClamp(value: unknown, lines: number) {
  const text = String(value || '')
  return h('span', { class: `message-clamp clamp-${lines}`, title: text }, text)
}

async function copyAiScript(value: unknown, event?: MouseEvent | KeyboardEvent) {
  event?.stopPropagation()
  if (event instanceof KeyboardEvent) event.preventDefault()
  const text = String(value || '').trim()
  if (!text) {
    ElMessage.warning('暂无AI话术可复制')
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('AI话术已复制')
  } catch {
    ElMessage.error('复制失败，请检查浏览器剪贴板权限')
  }
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
