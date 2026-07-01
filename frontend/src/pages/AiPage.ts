import { computed, defineComponent, h, reactive, ref, watch } from 'vue'
import { CopyDocument, Delete, MagicStick, Refresh, Search, View } from '@element-plus/icons-vue'
import type { Dict } from '../shared/types'
import { platformName } from '../shared/format'
import { SplitPane } from '../components/ui/SplitPane'

const PAGE_SIZE_OPTIONS = [10, 20, 50]

export default defineComponent({
  props: {
    workbench: { type: Object, default: () => ({}) },
    jobs: { type: Array, default: () => [] },
    leadRows: { type: Array, default: () => [] },
    competitorRows: { type: Array, default: () => [] }
  },
  emits: ['create-job', 'create-batch-jobs', 'delete-non-competitors', 'delete-non-customers', 'retry-job', 'retry-jobs'],
  setup(props, { emit }) {
    const activeTab = ref<'competitors' | 'leads' | 'failed' | 'history'>('competitors')
    const selected = ref<Dict | null>(null)
    const filters = reactive({
      keyword: '',
      status: '',
      result: '',
      page: 1,
      pageSize: 10
    })

    const summary = computed(() => props.workbench?.summary || {})
    const tabRows = computed(() => {
      if (activeTab.value === 'competitors') return props.workbench?.competitors || []
      if (activeTab.value === 'leads') return props.workbench?.leads || []
      if (activeTab.value === 'failed') return props.workbench?.failed_jobs || []
      return props.workbench?.history || []
    })
    const filteredRows = computed(() => filterRows(tabRows.value || [], activeTab.value, filters))
    const totalPages = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / filters.pageSize)))
    const pagedRows = computed(() => {
      const page = Math.min(filters.page, totalPages.value)
      const start = (page - 1) * filters.pageSize
      return filteredRows.value.slice(start, start + filters.pageSize)
    })
    const pageStart = computed(() => filteredRows.value.length ? (Math.min(filters.page, totalPages.value) - 1) * filters.pageSize + 1 : 0)
    const pageEnd = computed(() => Math.min(filteredRows.value.length, Math.min(filters.page, totalPages.value) * filters.pageSize))

    watch(activeTab, () => {
      filters.status = ''
      filters.result = ''
      filters.page = 1
      selected.value = null
    })
    watch(() => [filters.keyword, filters.status, filters.result], () => {
      filters.page = 1
    })

    function runSingle(row: Dict) {
      emit('create-job', row.target_type, Number(row.target_id || row.id))
    }
    function runBatch() {
      const targetType = activeTab.value === 'leads' ? 'lead' : 'competitor'
      const ids = filteredRows.value
        .filter(row => row.target_type === targetType)
        .filter(row => ['未分析', '失败'].includes(String(row.analysis_status || '')))
        .map(row => Number(row.target_id || row.id))
      emit('create-batch-jobs', targetType, ids)
    }
    function deleteNonCompetitors() {
      const ids = filteredRows.value
        .filter(row => row.target_type === 'competitor')
        .filter(isNonCompetitorRow)
        .map(row => Number(row.target_id || row.id))
      emit('delete-non-competitors', ids)
    }
    function deleteNonCustomers() {
      const ids = filteredRows.value
        .filter(row => row.target_type === 'lead')
        .filter(isNonCustomerRow)
        .map(row => Number(row.target_id || row.id))
      emit('delete-non-customers', ids)
    }
    function retryBatch() {
      const ids = filteredRows.value
        .filter(row => row.status === 'failed' || row.job_status === 'failed')
        .map(row => String(row.id || row.job_id || ''))
        .filter(Boolean)
      emit('retry-jobs', ids)
    }
    function retryOne(row: Dict) {
      emit('retry-job', row.id || row.job_id)
    }
    function copyScript(row: Dict) {
      const script = String(row.result_script || row.script || '')
      if (script) navigator.clipboard?.writeText(script)
    }

    return () => h(SplitPane, { storageKey: 'ai-workbench', side: 'right', defaultSideWidth: 360 }, {
      default: () => h('section', { class: 'pane ai-workbench' }, [
        renderSummary(summary.value),
        renderTabs(activeTab.value, tab => activeTab.value = tab),
        renderToolbar(activeTab.value, filters, runBatch, retryBatch, deleteNonCompetitors, deleteNonCustomers),
        renderRows({
          tab: activeTab.value,
          rows: pagedRows.value,
          selected: selected.value,
          onSelect: row => selected.value = row,
          onRun: runSingle,
          onRetry: retryOne,
          onCopy: copyScript
        }),
        renderPagination(filters, filteredRows.value.length, pageStart.value, pageEnd.value, totalPages.value)
      ]),
      side: () => renderDetailPane(selected.value, activeTab.value)
    })
  }
})

function renderSummary(summary: Dict) {
  const cards = [
    ['待分析竞品', summary.competitor_pending || 0, 'accent-amber'],
    ['待分析客户', summary.lead_pending || 0, 'accent-green'],
    ['正在分析', summary.running || 0, 'accent-blue'],
    ['失败待重试', summary.failed || 0, 'accent-red'],
    ['今日已分析', summary.succeeded_today || 0, 'accent-purple'],
    ['并行数', summary.concurrency || 1, 'accent-gray']
  ]
  return h('div', { class: 'ai-summary-grid' }, cards.map(([label, value, cls]) => h('div', { class: ['metric-tile', cls] }, [
    h('small', label),
    h('strong', String(value))
  ])))
}

function renderTabs(active: string, setActive: (tab: 'competitors' | 'leads' | 'failed' | 'history') => void) {
  const tabs: Array<['competitors' | 'leads' | 'failed' | 'history', string]> = [
    ['competitors', '竞品账号分析'],
    ['leads', '客户意向分析'],
    ['failed', '失败与重试'],
    ['history', '分析历史']
  ]
  return h('div', { class: 'ai-tabs' }, tabs.map(([key, label]) => h('button', {
    class: active === key ? 'active' : '',
    type: 'button',
    onClick: () => setActive(key)
  }, label)))
}

function renderToolbar(
  tab: string,
  filters: Dict,
  runBatch: () => void,
  retryBatch: () => void,
  deleteNonCompetitors: () => void,
  deleteNonCustomers: () => void
) {
  const statusOptions = tab === 'history'
    ? ['succeeded', 'failed']
    : tab === 'failed'
      ? ['配置错误', 'JSON解析失败', '网络或接口错误', '对象已删除', '其它失败']
      : ['未分析', '排队分析', '正在分析', '已分析', '失败']
  const resultOptions = tab === 'competitors'
    ? ['竞品', '非竞品', '-']
    : tab === 'leads'
      ? ['目标客户', '非客户', '-']
      : []
  return h('div', { class: 'ai-toolbar' }, [
    h('div', { class: 'ai-search' }, [
      h(Search),
      h('input', {
        value: filters.keyword,
        placeholder: tab === 'failed' ? '搜索任务、对象、错误原因' : '搜索名称、简介、评论、来源、原因',
        onInput: (event: Event) => filters.keyword = (event.target as HTMLInputElement).value
      })
    ]),
    h('select', { value: filters.status, onChange: (event: Event) => filters.status = (event.target as HTMLSelectElement).value }, [
      h('option', { value: '' }, tab === 'failed' ? '全部失败类型' : '全部状态'),
      ...statusOptions.map(status => h('option', { value: status }, status))
    ]),
    resultOptions.length ? h('select', { value: filters.result, onChange: (event: Event) => filters.result = (event.target as HTMLSelectElement).value }, [
      h('option', { value: '' }, '全部结论'),
      ...resultOptions.map(result => h('option', { value: result }, result))
    ]) : null,
    ['competitors', 'leads'].includes(tab) ? h('button', { class: 'primary-action', type: 'button', onClick: runBatch }, [
      h(MagicStick, { class: 'inline-icon' }),
      tab === 'competitors' ? '批量分析当前筛选' : '批量意向分析'
    ]) : null,
    tab === 'competitors' ? h('button', { class: 'primary-action ai-danger-action', type: 'button', onClick: deleteNonCompetitors }, [
      h(Delete, { class: 'inline-icon' }),
      '删除非竞品'
    ]) : null,
    tab === 'leads' ? h('button', { class: 'primary-action ai-danger-action', type: 'button', onClick: deleteNonCustomers }, [
      h(Delete, { class: 'inline-icon' }),
      '删除非客户'
    ]) : null,
    tab === 'failed' ? h('button', { class: 'primary-action', type: 'button', onClick: retryBatch }, [
      h(Refresh, { class: 'inline-icon' }),
      '重试当前失败'
    ]) : null
  ])
}

function renderRows(args: {
  tab: string
  rows: Dict[]
  selected: Dict | null
  onSelect: (row: Dict) => void
  onRun: (row: Dict) => void
  onRetry: (row: Dict) => void
  onCopy: (row: Dict) => void
}) {
  if (!args.rows.length) return h('div', { class: 'empty-state ai-empty' }, '当前筛选下没有数据')
  return h('div', { class: 'ai-row-list' }, args.rows.map(row => {
    const active = selectedKey(args.selected) === selectedKey(row)
    if (args.tab === 'failed') return renderFailedRow(row, active, args)
    if (args.tab === 'history') return renderHistoryRow(row, active, args)
    if (args.tab === 'leads') return renderLeadRow(row, active, args)
    return renderCompetitorRow(row, active, args)
  }))
}

function renderCompetitorRow(row: Dict, active: boolean, args: Dict) {
  return h('article', { class: ['ai-object-row', active ? 'active' : ''], onClick: () => args.onSelect(row) }, [
    h('div', { class: 'ai-object-main' }, [
      renderObjectTitle(row.nickname || `账号 ${row.id}`, row.profile_url),
      h('small', { title: row.signature || '' }, row.signature || '暂无主页简介'),
      h('div', { class: 'ai-row-tags' }, [
        row.platform ? h('span', platformName(row.platform)) : null,
        row.source_keywords ? h('span', `关键词 ${row.source_keywords}`) : null,
        h('span', `内容 ${row.content_count || 0}`),
        h('span', `评论 ${row.comment_count || 0}`)
      ])
    ]),
    h('div', { class: 'ai-object-reason', title: row.result_reason || row.competitor_reason || '' }, [
      h('small', 'AI分析原因'),
      h('span', row.result_reason || row.competitor_reason || '暂无分析原因')
    ]),
    h('div', { class: 'ai-object-status' }, [
      h('span', { class: ['ai-status-pill', statusClass(row.analysis_status)] }, row.analysis_status || '未分析'),
      h('b', row.result_label || '-')
    ]),
    h('div', { class: 'ai-object-actions' }, [
      h('button', { type: 'button', class: 'overview-action', disabled: ['排队分析', '正在分析'].includes(row.analysis_status), onClick: stop(() => args.onRun(row)) }, '分析'),
      row.job_status === 'failed' ? h('button', { type: 'button', class: 'overview-action', onClick: stop(() => args.onRetry(row)) }, '重试') : null,
      h('button', { type: 'button', class: 'icon-button', title: '查看详情', onClick: stop(() => args.onSelect(row)) }, [h(View)])
    ])
  ])
}

function renderLeadRow(row: Dict, active: boolean, args: Dict) {
  return h('article', { class: ['ai-object-row', 'lead-row', active ? 'active' : ''], onClick: () => args.onSelect(row) }, [
    h('div', { class: 'ai-object-main' }, [
      renderObjectTitle(row.nickname || `客户 ${row.id}`, row.profile_url),
      h('small', { title: row.comment_samples || '' }, firstLines(row.comment_samples, '暂无评论证据')),
      h('div', { class: 'ai-row-tags' }, [
        row.platform ? h('span', platformName(row.platform)) : null,
        row.source_account_names ? h('span', `来源 ${firstLines(row.source_account_names)}`) : null,
        h('span', `证据 ${row.source_count || 0}`),
        row.intention ? h('span', `意向 ${row.intention}`) : null
      ])
    ]),
    h('div', { class: 'ai-object-reason', title: row.content_samples || '' }, [
      h('small', '来源视频'),
      h('span', firstLines(row.content_samples, '暂无视频详情'))
    ]),
    h('div', { class: 'ai-object-reason', title: row.result_reason || row.reason || '' }, [
      h('small', 'AI分析原因'),
      h('span', row.result_reason || row.reason || '暂无分析原因')
    ]),
    h('div', { class: 'ai-object-status' }, [
      h('span', { class: ['ai-status-pill', statusClass(row.analysis_status)] }, row.analysis_status || '未分析'),
      h('b', row.result_label || row.follow_status || '-')
    ]),
    h('div', { class: 'ai-object-actions' }, [
      h('button', { type: 'button', class: 'overview-action', disabled: ['排队分析', '正在分析'].includes(row.analysis_status), onClick: stop(() => args.onRun(row)) }, '意向分析'),
      row.result_script || row.script ? h('button', { type: 'button', class: 'icon-button', title: '复制话术', onClick: stop(() => args.onCopy(row)) }, [h(CopyDocument)]) : null,
      row.job_status === 'failed' ? h('button', { type: 'button', class: 'overview-action', onClick: stop(() => args.onRetry(row)) }, '重试') : null
    ])
  ])
}

function renderFailedRow(row: Dict, active: boolean, args: Dict) {
  return h('article', { class: ['ai-job-row', active ? 'active' : ''], onClick: () => args.onSelect(row) }, [
    h('div', { class: 'ai-job-title' }, [
      h('strong', row.target_name || row.id),
      h('small', `${targetTypeLabel(row.target_type)} #${row.target_id}`)
    ]),
    h('div', { class: 'ai-job-error', title: row.error || '' }, [
      h('span', { class: 'ai-error-type' }, row.error_category || '其它失败'),
      h('p', row.error || '暂无错误信息')
    ]),
    h('div', { class: 'ai-object-actions' }, [
      h('button', { type: 'button', class: 'overview-action', onClick: stop(() => args.onRetry(row)) }, '重试'),
      h('button', { type: 'button', class: 'icon-button', title: '查看详情', onClick: stop(() => args.onSelect(row)) }, [h(View)])
    ])
  ])
}

function renderHistoryRow(row: Dict, active: boolean, args: Dict) {
  return h('article', { class: ['ai-job-row', active ? 'active' : ''], onClick: () => args.onSelect(row) }, [
    h('div', { class: 'ai-job-title' }, [
      h('strong', row.target_name || row.id),
      h('small', `${targetTypeLabel(row.target_type)} #${row.target_id}`)
    ]),
    h('div', { class: 'ai-job-error', title: row.output_summary || row.error || '' }, [
      h('span', { class: ['ai-status-pill', row.status === 'succeeded' ? 'is-done' : 'is-failed'] }, row.status),
      h('p', row.output_summary || row.error || '-')
    ]),
    h('div', { class: 'ai-job-time' }, row.updated_at || row.created_at || '-')
  ])
}

function renderPagination(filters: Dict, total: number, start: number, end: number, totalPages: number) {
  return h('div', { class: 'table-pagination ai-pagination' }, [
    h('div', { class: 'table-page-size' }, [
      h('span', total ? `${start}-${end} / ${total}` : '0 条'),
      h('select', { value: String(filters.pageSize), onChange: (event: Event) => { filters.pageSize = Number((event.target as HTMLSelectElement).value); filters.page = 1 } }, PAGE_SIZE_OPTIONS.map(size => h('option', { value: String(size) }, `${size}条`)))
    ]),
    h('div', { class: 'table-page-controls' }, [
      h('button', { type: 'button', disabled: filters.page <= 1, onClick: () => filters.page = Math.max(1, filters.page - 1) }, '上一页'),
      h('span', `${Math.min(filters.page, totalPages)} / ${totalPages}`),
      h('button', { type: 'button', disabled: filters.page >= totalPages, onClick: () => filters.page = Math.min(totalPages, filters.page + 1) }, '下一页')
    ])
  ])
}

function renderDetailPane(row: Dict | null, tab: string) {
  if (!row) return h('aside', { class: 'pane side-pane ai-detail-pane' }, [
    h('div', { class: 'section-title' }, [h('h2', '分析详情'), h('span', '选择左侧条目')]),
    h('div', { class: 'empty-state' }, '点击任意账号、客户或失败任务查看输入证据和分析结果')
  ])
  return h('aside', { class: 'pane side-pane ai-detail-pane' }, [
    h('div', { class: 'section-title' }, [h('h2', detailTitle(row, tab)), h('span', row.analysis_status || row.status || '')]),
    renderDetailBlock('对象', [
      ['平台', row.platform ? platformName(row.platform) : '-'],
      ['类型', targetTypeLabel(row.target_type || (tab === 'leads' ? 'lead' : 'competitor'))],
      ['ID', row.target_id || row.id || '-'],
      ['链接', row.profile_url || row.target_url || '']
    ]),
    renderDetailBlock('证据', [
      ['主页简介', row.signature || row.target_summary || ''],
      ['来源关键词', row.source_keywords || ''],
      ['来源视频', firstLines(row.content_samples, '')],
      ['评论内容', firstLines(row.comment_samples, '')],
      ['来源竞品', firstLines(row.source_account_names, '')]
    ]),
    renderDetailBlock('AI结果', [
      ['结论', row.result_label || row.output_summary || '-'],
      ['原因', row.result_reason || row.reason || row.error || ''],
      ['话术', row.result_script || row.script || ''],
      ['任务', row.job_id || row.id || '-'],
      ['更新时间', row.job_updated_at || row.updated_at || '-']
    ]),
    renderPromptReview(row)
  ])
}

function renderDetailBlock(title: string, rows: Array<[string, unknown]>) {
  const visible = rows.filter(([, value]) => String(value || '').trim())
  if (!visible.length) return null
  return h('div', { class: 'ai-detail-block' }, [
    h('h3', title),
    ...visible.map(([label, value]) => {
      const text = String(value || '')
      if (label === '链接') return h('div', [h('small', label), h('a', { href: text, target: '_blank', rel: 'noreferrer' }, '打开链接')])
      return h('div', [h('small', label), h('p', { title: text }, text)])
    })
  ])
}

function renderPromptReview(row: Dict) {
  const hasPrompt = row.prompt_version || row.system_prompt || row.user_prompt || row.input_payload || row.raw_output || row.output_payload
  if (!hasPrompt) return null
  const promptText = [
    `Prompt Version: ${row.prompt_version || '-'}`,
    `Model: ${row.model || '-'}`,
    `Base URL: ${row.base_url || '-'}`,
    '',
    'System Prompt:',
    row.system_prompt || '',
    '',
    'User Prompt:',
    row.user_prompt || '',
  ].join('\n')
  return h('div', { class: 'ai-detail-block ai-prompt-review' }, [
    h('div', { class: 'ai-prompt-head' }, [
      h('h3', '提示词回看'),
      h('button', {
        type: 'button',
        class: 'overview-action',
        onClick: () => navigator.clipboard?.writeText(promptText)
      }, '复制提示词')
    ]),
    renderPromptKV('版本', row.prompt_version || '-'),
    renderPromptKV('模型', row.model || '-'),
    renderPromptKV('Base URL', row.base_url || '-'),
    renderPromptPre('输入 payload', row.input_payload),
    renderPromptPre('System Prompt', row.system_prompt),
    renderPromptPre('User Prompt', row.user_prompt),
    renderPromptPre('AI原始输出', row.raw_output),
    renderPromptPre('解析结果', row.output_payload)
  ])
}

function renderPromptKV(label: string, value: unknown) {
  return h('div', [h('small', label), h('p', String(value || '-'))])
}

function renderPromptPre(label: string, value: unknown) {
  const text = formatPayload(value)
  if (!text.trim()) return null
  return h('div', { class: 'prompt-pre-wrap' }, [
    h('small', label),
    h('pre', text)
  ])
}

function formatPayload(value: unknown) {
  if (!value) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function renderObjectTitle(label: string, href: string) {
  if (href) return h('a', { class: 'ai-object-title', href, target: '_blank', rel: 'noreferrer', title: label }, label)
  return h('strong', { class: 'ai-object-title', title: label }, label)
}

function filterRows(rows: Dict[], tab: string, filters: Dict) {
  const keyword = String(filters.keyword || '').trim().toLowerCase()
  return rows.filter(row => {
    const statusValue = tab === 'failed' ? row.error_category : tab === 'history' ? row.status : row.analysis_status
    const resultValue = tab === 'history' ? '' : row.result_label
    if (filters.status && statusValue !== filters.status) return false
    if (filters.result && resultValue !== filters.result) return false
    if (!keyword) return true
    return [
      row.id,
      row.job_id,
      row.nickname,
      row.target_name,
      row.signature,
      row.comment_samples,
      row.content_samples,
      row.source_keywords,
      row.source_account_names,
      row.result_reason,
      row.reason,
      row.error,
      row.output_summary
    ].some(value => String(value || '').toLowerCase().includes(keyword))
  })
}

function isNonCompetitorRow(row: Dict) {
  return row.result_label === '非竞品' || row.competitor_status === '非竞品'
}

function isNonCustomerRow(row: Dict) {
  return row.result_label === '非客户' || row.screening_status === '非客户' || ['非客户', '无需跟进'].includes(String(row.follow_status || ''))
}

function selectedKey(row: Dict | null) {
  if (!row) return ''
  return `${row.target_type || row.status || ''}:${row.target_id || row.id || row.job_id || ''}`
}

function firstLines(value: unknown, fallback = '-') {
  const text = String(value || '').trim()
  if (!text) return fallback
  return text.split(',').map(item => item.trim()).filter(Boolean).slice(0, 3).join('\n')
}

function statusClass(status: string) {
  if (status === '排队分析') return 'is-queued'
  if (status === '正在分析') return 'is-running'
  if (status === '已分析') return 'is-done'
  if (status === '失败') return 'is-failed'
  return 'is-unknown'
}

function targetTypeLabel(type: string) {
  return ({ competitor: '竞品账号', lead: '客户线索', content: '内容' } as Record<string, string>)[type] || type || '-'
}

function detailTitle(row: Dict, tab: string) {
  if (row.nickname) return row.nickname
  if (row.target_name) return row.target_name
  if (tab === 'failed') return '失败任务详情'
  return '分析详情'
}

function stop(callback: () => void) {
  return (event: MouseEvent) => {
    event.stopPropagation()
    callback()
  }
}
