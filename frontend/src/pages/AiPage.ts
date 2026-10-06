import { computed, defineComponent, h, reactive, ref, watch } from 'vue'
import type { Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElDrawer } from 'element-plus'
import { CircleCheck, CopyDocument, DataAnalysis, Delete, Finished, MagicStick, Refresh, Search, TrendCharts, User, View, Warning } from '@element-plus/icons-vue'
import type { Dict } from '../shared/types'
import { api } from '../shared/api'
import { platformName } from '../shared/format'
import '../styles/operations.css'
import { ListPagination, type PageChange } from '../components/ui/ListPagination'
import { emptyState, metricTile, sectionTitle, type WorkbenchTone } from '../components/ui/Workbench'

export default defineComponent({
  name: 'AiPage',
  props: {
    workbench: { type: Object, default: () => ({}) },
    query: { type: Object, default: () => ({ tab: 'competitors', keyword: '', status: '', result: '', page: 1, page_size: 10 }) }
  },
  emits: ['create-job', 'create-batch-jobs', 'delete-non-competitors', 'delete-non-customers', 'retry-job', 'retry-jobs', 'query-change'],
  setup(props, { emit }) {
    const route = useRoute()
    const router = useRouter()
    const activeTab = ref<'competitors' | 'leads' | 'failed' | 'history'>('competitors')
    const selected = ref<Dict | null>(null)
    const selectedJobId = ref('')
    const jobError = ref('')
    let detailSeq = 0
    let jobRequestId = ''
    let jobRequest: Promise<void> | undefined
    const filters = reactive({
      keyword: '',
      status: '',
      result: '',
      page: 1,
      pageSize: 10
    })

    const summary = computed(() => props.workbench?.summary || {})
    const rows = computed<Dict[]>(() => props.workbench?.items || [])
    const total = computed(() => Number(props.workbench?.total || 0))
    watch(rows, items => {
      if (selected.value && !selectedJobId.value) selected.value = items.find(row => selectedKey(row) === selectedKey(selected.value)) || null
    })
    watch(() => [route.name, route.query.job] as const, ([name, value]) => {
      // 缓存期间不响应其它页面的参数，回到本页才更新当前详情。
      if (name !== 'ai' || String(value || '') === selectedJobId.value) return
      const previous = selectedJobId.value
      selectedJobId.value = String(value || '')
      jobError.value = ''
      if (value) { selected.value = null; void loadJob(selectedJobId.value) }
      else { detailSeq += 1; jobRequestId = ''; jobRequest = undefined; if (previous) selected.value = null }
    }, { immediate: true })
    watch(() => props.workbench, () => { if (route.name === 'ai' && selectedJobId.value) void loadJob(selectedJobId.value) })

    async function loadJob(id: string) {
      if (jobRequestId === id && jobRequest) return jobRequest
      const revision = ++detailSeq
      jobRequestId = id
      const request = (async () => {
        try {
          const { data } = await api.get(`/ai/jobs/${encodeURIComponent(id)}`)
          // 任务中心直达详情独立于当前列表页，较慢旧请求不替换新选择。
          if (revision === detailSeq && selectedJobId.value === id) { selected.value = data; jobError.value = '' }
        } catch (error: any) {
          if (revision === detailSeq) jobError.value = error?.response?.data?.detail || '分析任务详情读取失败，请重试'
        } finally {
          if (revision === detailSeq) { jobRequestId = ''; jobRequest = undefined }
        }
      })()
      jobRequest = request
      return request
    }
    function selectRow(row: Dict) {
      detailSeq += 1
      jobRequestId = ''; jobRequest = undefined
      selectedJobId.value = ''
      jobError.value = ''
      selected.value = row
      if (route.query.job) { const { job, ...query } = route.query; void router.replace({ path: '/ai', query }) }
    }
    function closeDetail() {
      detailSeq += 1
      jobRequestId = ''; jobRequest = undefined
      selectedJobId.value = ''
      selected.value = null
      jobError.value = ''
      if (route.query.job) { const { job, ...query } = route.query; void router.replace({ path: '/ai', query }) }
    }

    // 请求与工具栏共享父级查询；返回页面时不再显示空筛选配旧结果。
    watch(() => props.query, value => {
      activeTab.value = value.tab as typeof activeTab.value
      Object.assign(filters, { keyword: value.keyword || '', status: value.status || '', result: value.result || '', page: Number(value.page || 1), pageSize: Number(value.page_size || 10) })
    }, { immediate: true, deep: true })
    watch(() => [route.name, route.query] as const, ([name, value]) => {
      if (name !== 'ai' || !value.tab) return
      const next = { tab: String(value.tab), keyword: String(value.keyword || ''), status: String(value.status || ''), result: String(value.result || ''), page: Number(value.page || 1), page_size: Number(value.page_size || 10) }
      if (!['competitors', 'leads', 'failed', 'history'].includes(next.tab)) return
      if (Object.entries(next).some(([key, item]) => String(props.query[key] ?? '') !== String(item))) emit('query-change', next)
    }, { immediate: true })

    function requestRows(page = filters.page, pageSize = filters.pageSize) {
      const next = {
        tab: activeTab.value,
        keyword: filters.keyword,
        status: filters.status,
        result: filters.result,
        page,
        page_size: pageSize,
      }
      emit('query-change', next)
      void router.replace({ path: '/ai', query: next })
    }
    function selectTab(tab: 'competitors' | 'leads' | 'failed' | 'history') {
      activeTab.value = tab
      filters.status = ''
      filters.result = ''
      filters.page = 1
      selected.value = null
      requestRows(1)
    }
    function applyFilters() {
      filters.page = 1
      selected.value = null
      requestRows(1)
    }

    function runSingle(row: Dict) {
      emit('create-job', row.target_type, Number(row.target_id || row.id))
    }
    function runBatch() {
      const targetType = activeTab.value === 'leads' ? 'lead' : 'competitor'
      // 全量对象由后端选择，不受当前列表的搜索、状态或分页限制。
      emit('create-batch-jobs', targetType)
    }
    function deleteNonCompetitors() {
      const ids = rows.value
        .filter(row => row.target_type === 'competitor')
        .filter(isNonCompetitorRow)
        .map(row => Number(row.target_id || row.id))
      emit('delete-non-competitors', ids)
    }
    function deleteNonCustomers() {
      const ids = rows.value
        .filter(row => row.target_type === 'lead')
        .filter(isNonCustomerRow)
        .map(row => Number(row.target_id || row.id))
      emit('delete-non-customers', ids)
    }
    function retryBatch() {
      const ids = rows.value
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

    return () => h('div', { class: 'workspace-page operations-page' }, [
      h('section', { class: 'panel ai-workbench' }, [
        sectionTitle({ title: 'AI筛选', subtitle: '先筛竞品账号，再判断客户意向；分析任务在后台队列中执行。', icon: MagicStick }),
        h('div', { class: 'toolbar' }, [
          h('button', { onClick: () => router.push({ path: '/message-workbench', query: { query: filters.keyword, status: '待私信' } }) }, '前往客户跟进'),
        ]),
        h('details', { class: 'operations-summary' }, [h('summary', '处理概况'),
        renderSummary(summary.value),
        ]),
        renderTabs(activeTab.value, selectTab),
        renderToolbar(activeTab.value, filters, applyFilters, runBatch, retryBatch, deleteNonCompetitors, deleteNonCustomers),
        renderRows({
          tab: activeTab.value,
          rows: rows.value,
          selected: selected.value,
          onSelect: selectRow,
          onRun: runSingle,
          onRetry: retryOne,
          onCopy: copyScript,
          onFollow: row => router.push({ path: '/message-workbench', query: { customer: String(row.target_id || row.id), status: '全部' } }),
          onAcquire: row => router.push({ path: '/acquisition', query: { create: '1', platform: String(row.platform || 'dy'), creator: String(row.profile_url || row.sec_uid || row.platform_user_id || '') } }),
        }),
        renderPagination(
          filters,
          total.value,
          page => { filters.page = page; selected.value = null; requestRows(page) },
          pageSize => { filters.page = 1; filters.pageSize = pageSize; selected.value = null; requestRows(1, pageSize) },
        )
      ]),
      h(ElDrawer, { modelValue: route.name === 'ai' && Boolean(selected.value || selectedJobId.value), title: '分析详情', size: '760px', class: 'operations-detail', 'onUpdate:modelValue': (value: boolean) => { if (!value && route.name === 'ai') closeDetail() } }, () => [
        jobError.value ? h('div', { class: 'toolbar' }, [h('p', { class: 'operations-error' }, jobError.value), h('button', { onClick: () => loadJob(selectedJobId.value) }, '重新读取详情')]) : null,
        selectedJobId.value && !selected.value && !jobError.value ? h('p', { class: 'note' }, '正在读取分析任务…') : renderDetailPane(selected.value, selectedJobId.value ? (selected.value?.status === 'failed' ? 'failed' : 'history') : activeTab.value),
      ]),
    ])
  }
})

function renderSummary(summary: Dict) {
  const cards: Array<{ label: string, value: string | number, icon: Component, tone: WorkbenchTone }> = [
    { label: '待分析竞品', value: summary.competitor_pending || 0, icon: TrendCharts, tone: 'amber' },
    { label: '待分析客户', value: summary.lead_pending || 0, icon: User, tone: 'green' },
    { label: '正在分析', value: summary.running || 0, icon: MagicStick, tone: 'blue' },
    { label: '失败待重试', value: summary.failed || 0, icon: Warning, tone: 'red' },
    { label: '今日已分析', value: summary.succeeded_today || 0, icon: CircleCheck, tone: 'purple' },
    { label: '并行数', value: summary.concurrency || 1, icon: Finished, tone: 'gray' }
  ]
  return h('div', { class: 'ai-summary-grid' }, cards.map(card => metricTile(card)))
}

function renderTabs(active: string, setActive: (tab: 'competitors' | 'leads' | 'failed' | 'history') => void) {
  const tabs: Array<['competitors' | 'leads' | 'failed' | 'history', string]> = [
    ['competitors', '竞品账号分析'],
    ['leads', '客户意向分析'],
    ['failed', '失败与重试'],
    ['history', '分析历史']
  ]
  const button = ([key, label]: typeof tabs[number]) => h('button', { class: active === key ? 'active' : '', type: 'button', onClick: () => setActive(key) }, label)
  return h('nav', { class: 'ai-tabs tabs' }, [
    ...tabs.slice(0, 2).map(button),
    h('div', { class: 'operations-secondary-tabs' }, tabs.slice(2).map(button)),
  ])
}

function renderToolbar(
  tab: string,
  filters: Dict,
  applyFilters: () => void,
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
        placeholder: tab === 'failed' ? '搜索任务、对象、错误；逗号分隔任一匹配' : '搜索名称、评论、来源；逗号分隔任一匹配',
        onInput: (event: Event) => filters.keyword = (event.target as HTMLInputElement).value,
        onKeydown: (event: KeyboardEvent) => { if (event.key === 'Enter') applyFilters() }
      })
    ]),
    h('select', { value: filters.status, onChange: (event: Event) => { filters.status = (event.target as HTMLSelectElement).value; applyFilters() } }, [
      h('option', { value: '' }, tab === 'failed' ? '全部失败类型' : '全部状态'),
      ...statusOptions.map(status => h('option', { value: status }, status))
    ]),
    resultOptions.length ? h('select', { value: filters.result, onChange: (event: Event) => { filters.result = (event.target as HTMLSelectElement).value; applyFilters() } }, [
      h('option', { value: '' }, '全部结论'),
      ...resultOptions.map(result => h('option', { value: result }, result))
    ]) : null,
    h('button', { class: 'filter-button', type: 'button', onClick: applyFilters }, '搜索'),
    ['competitors', 'leads'].includes(tab) ? h('button', { class: 'primary-action', type: 'button', title: '分析当前类型的全部未分析对象，不受筛选和分页限制', onClick: runBatch }, [
      h(MagicStick, { class: 'inline-icon' }),
      '一键分析'
    ]) : null,
    tab === 'competitors' ? h('button', { class: 'secondary-action ai-danger-action', type: 'button', onClick: deleteNonCompetitors }, [
      h(Delete, { class: 'inline-icon' }),
      '删除本页非竞品'
    ]) : null,
    tab === 'leads' ? h('button', { class: 'secondary-action ai-danger-action', type: 'button', onClick: deleteNonCustomers }, [
      h(Delete, { class: 'inline-icon' }),
      '删除本页非客户'
    ]) : null,
    tab === 'failed' ? h('button', { class: 'primary-action', type: 'button', onClick: retryBatch }, [
      h(Refresh, { class: 'inline-icon' }),
      '重试本页失败'
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
  onFollow: (row: Dict) => void
  onAcquire: (row: Dict) => void
}) {
  if (!args.rows.length) return emptyState({
    title: '当前筛选下没有数据',
    description: '切换状态、清空关键词或返回其它标签页查看待分析对象。',
    icon: Search,
    tone: 'gray'
  })
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
      h('button', { type: 'button', class: 'icon-button', title: '查看详情', 'aria-label': '查看详情', onClick: stop(() => args.onSelect(row)) }, [h(View)]),
      row.result_label === '竞品' && (row.profile_url || row.sec_uid || row.platform_user_id) ? h('button', { type: 'button', class: 'overview-action', onClick: stop(() => args.onAcquire(row)) }, '采集评论') : null
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
      row.result_script || row.script ? h('button', { type: 'button', class: 'icon-button', title: '复制话术', 'aria-label': '复制话术', onClick: stop(() => args.onCopy(row)) }, [h(CopyDocument)]) : null,
      row.job_status === 'failed' ? h('button', { type: 'button', class: 'overview-action', onClick: stop(() => args.onRetry(row)) }, '重试') : null,
      row.result_label === '目标客户' ? h('button', { type: 'button', class: 'overview-action', onClick: stop(() => args.onFollow(row)) }, '跟进') : null
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
      h('button', { type: 'button', class: 'icon-button', title: '查看详情', 'aria-label': '查看详情', onClick: stop(() => args.onSelect(row)) }, [h(View)])
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

function renderPagination(
  filters: Dict,
  total: number,
  changePage: (page: number) => void,
  changePageSize: (pageSize: number) => void,
) {
  return h(ListPagination, {
    page: Number(filters.page || 1),
    pageSize: Number(filters.pageSize || 10),
    total,
    onChange: (payload: PageChange) => payload.page_size === filters.pageSize
      ? changePage(payload.page)
      : changePageSize(payload.page_size),
  })
}

function renderDetailPane(row: Dict | null, tab: string) {
  if (!row) return h('aside', { class: 'pane side-pane ai-detail-pane' }, [
    sectionTitle({ title: '分析详情', subtitle: '选择左侧条目', icon: View, tone: 'blue' }),
    emptyState({
      title: '选择一条分析对象',
      description: '点击任意账号、客户或失败任务，右侧会显示输入证据和 AI 结果。',
      icon: DataAnalysis,
      tone: 'gray'
    })
  ])
  return h('aside', { class: 'pane side-pane ai-detail-pane' }, [
    sectionTitle({ title: detailTitle(row, tab), subtitle: row.analysis_status || row.status || '', icon: View, tone: tab === 'failed' ? 'red' : 'blue' }),
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
      ['原因', row.result_reason || row.reason || row.output_payload?.reason || row.error || ''],
      ['话术', row.result_script || row.script || row.output_payload?.script || ''],
      ['任务', row.job_id || row.id || '-'],
      ['更新时间', row.job_updated_at || row.updated_at || '-']
    ]),
    row.input_payload ? h('details', { class: 'operations-advanced' }, [h('summary', '本次分析的原始证据'), h('pre', JSON.stringify(row.input_payload, null, 2))]) : null,
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

function renderObjectTitle(label: string, href: string) {
  if (href) return h('a', { class: 'ai-object-title', href, target: '_blank', rel: 'noreferrer', title: label }, label)
  return h('strong', { class: 'ai-object-title', title: label }, label)
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
