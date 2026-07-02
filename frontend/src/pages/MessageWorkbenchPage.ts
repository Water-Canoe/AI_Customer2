import { computed, defineComponent, h, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Close, CopyDocument, Search } from '@element-plus/icons-vue'
import { SplitPane } from '../components/ui/SplitPane'
import { platformName } from '../shared/format'
import type { Dict } from '../shared/types'

const statusTabs = ['待私信', '已私信', '未回复', '已回复', '未成交', '已成交', '全部']
const keywordPageSize = 8

export default defineComponent({
  props: {
    keywords: { type: Array, default: () => [] },
    customers: { type: Object, default: () => ({ rows: [], total: 0, page: 1, page_size: 20, total_pages: 1 }) },
    detail: { type: Object, default: () => ({}) },
    filters: { type: Object, default: () => ({ keyword: '', status: '待私信', query: '', page: 1, page_size: 20 }) },
    loading: { type: Boolean, default: false },
  },
  emits: ['filter-change', 'select-customer', 'message-customer', 'update-follow-status', 'close-detail'],
  setup(props, { emit }) {
    const queryDraft = ref(String((props.filters as Dict).query || ''))
    const keywordPage = ref(1)
    watch(() => (props.filters as Dict).query, value => {
      queryDraft.value = String(value || '')
    })
    watch(() => props.keywords, () => {
      keywordPage.value = 1
    })

    const rows = computed(() => (props.customers as Dict).rows || [])
    const total = computed(() => Number((props.customers as Dict).total || 0))
    const page = computed(() => Number((props.customers as Dict).page || 1))
    const pageSize = computed(() => Number((props.customers as Dict).page_size || 20))
    const totalPages = computed(() => Number((props.customers as Dict).total_pages || 1))
    const keywordTotal = computed(() => (props.keywords as Dict[]).length)
    const keywordTotalPages = computed(() => Math.max(1, Math.ceil(keywordTotal.value / keywordPageSize)))
    const normalizedKeywordPage = computed(() => Math.min(keywordPage.value, keywordTotalPages.value))
    const pagedKeywords = computed(() => {
      const start = (normalizedKeywordPage.value - 1) * keywordPageSize
      return (props.keywords as Dict[]).slice(start, start + keywordPageSize)
    })

    function changeFilter(next: Dict) {
      emit('filter-change', { ...(props.filters as Dict), ...next })
    }

    function runSearch() {
      changeFilter({ query: queryDraft.value.trim(), page: 1 })
    }

    return () => h(SplitPane, { storageKey: 'message-workbench', side: 'left', defaultSideWidth: 300, minSideWidth: 260, maxSideWidth: 420 }, {
      side: () => h('aside', { class: 'pane message-keyword-pane' }, [
        h('div', { class: 'section-title' }, [
          h('h2', '关键词队列'),
          h('span', '按需求产品筛选')
        ]),
        h('div', { class: 'message-keyword-list' }, pagedKeywords.value.map(keyword => renderKeywordButton(keyword, props.filters as Dict, changeFilter))),
        h('div', { class: 'keyword-pagination' }, [
          h('span', `共 ${keywordTotal.value} 个关键词`),
          h('div', [
            h('button', {
              type: 'button',
              disabled: normalizedKeywordPage.value <= 1,
              onClick: () => keywordPage.value = Math.max(1, normalizedKeywordPage.value - 1)
            }, '上一页'),
            h('strong', `${normalizedKeywordPage.value} / ${keywordTotalPages.value}`),
            h('button', {
              type: 'button',
              disabled: normalizedKeywordPage.value >= keywordTotalPages.value,
              onClick: () => keywordPage.value = Math.min(keywordTotalPages.value, normalizedKeywordPage.value + 1)
            }, '下一页')
          ])
        ])
      ]),
      default: () => h('section', { class: 'pane message-workbench' }, [
        h('div', { class: 'message-workbench-head' }, [
          h('div', [
            h('h2', '私信工作台'),
            h('p', '按关键词推进客户私信、回访和成交状态')
          ]),
          h('div', { class: 'message-search' }, [
            h('input', {
              value: queryDraft.value,
              placeholder: '搜索客户、评论、视频、话术',
              onInput: (event: Event) => queryDraft.value = (event.target as HTMLInputElement).value,
              onKeydown: (event: KeyboardEvent) => {
                if (event.key === 'Enter') runSearch()
              }
            }),
            h('button', { type: 'button', onClick: runSearch }, [h(Search), h('span', '搜索')])
          ])
        ]),
        h('div', { class: 'message-status-tabs' }, statusTabs.map(status => h('button', {
          type: 'button',
          class: String((props.filters as Dict).status || '待私信') === status ? 'active' : '',
          onClick: () => changeFilter({ status, page: 1 })
        }, status))),
        renderCustomerTable(rows.value, props.loading, emit),
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
  const selected = String(filters.keyword || '') === String(keyword.keyword || '')
  return h('button', {
    type: 'button',
    class: ['message-keyword-item', selected ? 'active' : ''],
    onClick: () => changeFilter({ keyword: keyword.keyword || '', page: 1 })
  }, [
    h('div', [
      h('strong', keyword.label || keyword.keyword || '全部'),
      h('small', `${keyword.customer_count || 0} 个客户`)
    ]),
    h('div', { class: 'keyword-metrics' }, [
      h('span', `待私信 ${keyword.unmessaged_count || 0}`),
      h('span', `未回复 ${keyword.waiting_reply_count || 0}`),
      keyword.overdue_count ? h('span', { class: 'warn' }, `超时 ${keyword.overdue_count}`) : null
    ])
  ])
}

function renderCustomerTable(rows: Dict[], loading: boolean, emit: any) {
  const body = loading
    ? [h('tr', [h('td', { colspan: 6, class: 'message-empty' }, '加载中...')])]
    : rows.length
      ? rows.map(row => renderCustomerRow(row, emit))
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

function renderCustomerRow(row: Dict, emit: any) {
  const rawScript = String(row.script || '').trim()
  const script = rawScript || '暂无AI话术'
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
            class: 'text-icon-button',
            disabled: !row.script || !row.profile_url,
            title: !row.script ? '暂无AI话术' : !row.profile_url ? '缺少客户主页' : '复制话术、打开主页并标记已私信',
            onClick: () => emit('message-customer', row)
          }, [h(CopyDocument), h('span', '私信')]),
          h('button', { type: 'button', class: 'ghost-button compact', onClick: () => emit('select-customer', row.lead_id) }, '详情')
        ]),
        h('select', {
          class: ['follow-select', followStatusClass(row.follow_status)],
          value: row.follow_status || '未私信',
          onChange: (event: Event) => emit('update-follow-status', row, (event.target as HTMLSelectElement).value)
        }, followOptions(row.follow_status).map(status => h('option', { value: status }, status))),
        row.overdue ? h('span', { class: 'overdue-badge' }, `超时 ${row.overdue_days || 0} 天`) : null
      ])
    ])
  ])
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
      h('button', { type: 'button', class: 'icon-button', onClick: () => emit('close-detail') }, [h(Close)])
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
