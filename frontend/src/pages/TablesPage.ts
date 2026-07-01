import { defineComponent, h, onBeforeUnmount, reactive, watch } from 'vue'
import { CopyDocument, Delete, MagicStick, Refresh, Search } from '@element-plus/icons-vue'
import { computed, ref } from 'vue'
import type { Dict } from '../shared/types'
import { clamp } from '../shared/format'

export default defineComponent({
  props: {
    library: { type: String, required: true },
    rows: { type: Array, required: true },
    loading: { type: Boolean, required: true },
    statusFilter: { type: String, default: '' },
    keywordFilter: { type: String, default: '' }
  },
  emits: ['change-library', 'change-filter', 'update-row', 'delete-row', 'analyze-row', 'enrich-profile', 'find-customers'],
  setup(props, { emit }) {
    const libraries = [
      ['contents', '内容库'],
      ['comments', '评论库'],
      ['competitor_candidates', '竞品候选库'],
      ['competitors', '竞品库'],
      ['lead_customers', '线索客户库'],
      ['target_customers', '目标客户库']
    ]
    const accountLibraries = ['competitor_candidates', 'competitors', 'lead_customers', 'target_customers']
    const baseHeaders = ['名称/内容', '状态', '来源任务', '证据/链接', '操作']
    const competitorHeaders = ['名称/内容', '简介', 'AI分析原因', '粉丝数', '状态', '来源任务', '证据/链接', '操作']
    const defaultWidths = [320, 120, 190, 150, 170]
    const competitorWidths = [220, 260, 280, 110, 100, 180, 140, 190]
    const widthsByLibrary = reactive<Record<string, number[]>>({})
    const filterDraft = reactive({ status: props.statusFilter, keyword: props.keywordFilter })
    const currentPage = ref(1)
    const pageSize = ref(10)
    let stopColumnResize: (() => void) | null = null
    watch(() => [props.statusFilter, props.keywordFilter], ([status, keyword]) => {
      filterDraft.status = String(status || '')
      filterDraft.keyword = String(keyword || '')
    })
    function columnWidths() {
      if (!widthsByLibrary[props.library]) {
        widthsByLibrary[props.library] = props.library === 'competitors' ? [...competitorWidths] : [...defaultWidths]
      }
      return widthsByLibrary[props.library]
    }
    function headers() {
      return props.library === 'competitors' ? competitorHeaders : baseHeaders
    }
    const totalRows = computed(() => (props.rows as Dict[]).length)
    const totalPages = computed(() => Math.max(1, Math.ceil(totalRows.value / pageSize.value)))
    const pagedRows = computed(() => {
      const page = Math.min(currentPage.value, totalPages.value)
      const start = (page - 1) * pageSize.value
      return (props.rows as Dict[]).slice(start, start + pageSize.value)
    })
    const pageStart = computed(() => totalRows.value ? (Math.min(currentPage.value, totalPages.value) - 1) * pageSize.value + 1 : 0)
    const pageEnd = computed(() => Math.min(totalRows.value, Math.min(currentPage.value, totalPages.value) * pageSize.value))
    watch(() => [props.library, props.rows, props.statusFilter, props.keywordFilter], () => {
      currentPage.value = 1
    })
    function statusOptions() {
      if (['competitor_candidates', 'competitors'].includes(props.library)) return ['未分析', '竞品', '非竞品']
      if (['lead_customers', 'target_customers'].includes(props.library)) return ['待筛选', '非客户', '未私信', '已私信', '未回复', '已回复', '未成交', '已成交', '无需跟进']
      return []
    }
    function applyFilters() {
      emit('change-filter', { status: filterDraft.status, keyword: filterDraft.keyword })
    }
    function resetFilters() {
      filterDraft.status = ''
      filterDraft.keyword = ''
      emit('change-filter', { status: '', keyword: '' })
    }
    function changePageSize(event: Event) {
      pageSize.value = Number((event.target as HTMLSelectElement).value)
      currentPage.value = 1
    }
    function previousPage() {
      currentPage.value = Math.max(1, currentPage.value - 1)
    }
    function nextPage() {
      currentPage.value = Math.min(totalPages.value, currentPage.value + 1)
    }
    function startColumnResize(index: number, event: PointerEvent) {
      event.preventDefault()
      const widths = columnWidths()
      const startX = event.clientX
      const startWidth = widths[index]
      const onMove = (moveEvent: PointerEvent) => {
        widths[index] = clamp(startWidth + moveEvent.clientX - startX, 90, 680)
      }
      const onUp = () => {
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        stopColumnResize = null
      }
      stopColumnResize = onUp
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
    }
    onBeforeUnmount(() => stopColumnResize?.())
    return () => h('section', { class: 'pane table-workspace' }, [
      h('div', { class: 'table-library-bar' }, [
        h('div', { class: 'section-title' }, [h('h2', '数据表'), h('span', '选择一个业务库')]),
        h('div', { class: 'library-list' }, libraries.map(([key, label]) => h('button', { class: props.library === key ? 'selected' : '', onClick: () => emit('change-library', key) }, label))),
        h('div', { class: 'table-filters' }, [
          statusOptions().length ? h('select', {
            value: filterDraft.status,
            onChange: (event: Event) => filterDraft.status = (event.target as HTMLSelectElement).value
          }, [
            h('option', { value: '' }, '全部状态'),
            ...statusOptions().map(status => h('option', { value: status }, status))
          ]) : null,
          h('input', {
            value: filterDraft.keyword,
            placeholder: '搜索名称、内容、简介或来源',
            onInput: (event: Event) => filterDraft.keyword = (event.target as HTMLInputElement).value,
            onKeydown: (event: KeyboardEvent) => {
              if (event.key === 'Enter') applyFilters()
            }
          }),
          h('button', { type: 'button', class: 'filter-button', onClick: applyFilters }, '筛选'),
          h('button', { type: 'button', class: 'ghost-button', onClick: resetFilters }, '清空')
        ])
      ]),
      h('div', { class: 'table-content' }, [
        h('div', { class: 'section-title' }, [
          h('h2', libraries.find(([key]) => key === props.library)?.[1] || '数据'),
          h('span', totalRows.value ? `${pageStart.value}-${pageEnd.value} / ${totalRows.value} 条记录` : '0 条记录')
        ]),
        h('div', { class: 'table-scroll' }, [
        h('table', { class: 'data-table resizable-table', style: { minWidth: `${columnWidths().reduce((total, width) => total + width, 0)}px` } }, [
          h('colgroup', columnWidths().map(width => h('col', { style: { width: `${width}px` } }))),
          h('thead', [h('tr', headers().map((label, index) => h('th', [
            h('span', label),
            h('button', { class: 'column-resizer', type: 'button', title: '拖动调整列宽', onPointerdown: (event: PointerEvent) => startColumnResize(index, event) })
          ])))]),
          h('tbody', pagedRows.value.length ? pagedRows.value.map(row => renderRow(props.library, row, emit, accountLibraries)) : [
            h('tr', [h('td', { class: 'table-empty', colspan: headers().length }, props.loading ? '加载中...' : '暂无数据')])
          ])
        ])
        ]),
        h('div', { class: 'table-pagination' }, [
          h('div', { class: 'table-page-size' }, [
            h('span', '每页'),
            h('select', { value: String(pageSize.value), onChange: changePageSize }, [
              h('option', { value: '10' }, '10'),
              h('option', { value: '20' }, '20'),
              h('option', { value: '50' }, '50')
            ]),
            h('span', '条')
          ]),
          h('div', { class: 'table-page-controls' }, [
            h('button', { type: 'button', disabled: currentPage.value <= 1, onClick: previousPage }, '上一页'),
            h('span', `${Math.min(currentPage.value, totalPages.value)} / ${totalPages.value}`),
            h('button', { type: 'button', disabled: currentPage.value >= totalPages.value, onClick: nextPage }, '下一页')
          ])
        ])
      ])
    ])
  }
})

function renderRow(library: string, row: Dict, emit: any, accountLibraries: string[]) {
  if (library === 'competitors') {
    return h('tr', [
      h('td', [renderTablePrimaryCell(library, row)]),
      h('td', [renderTruncatedText(row.signature || '-', 'table-muted-text')]),
      h('td', [renderTruncatedText(row.competitor_reason || '-', 'table-muted-text')]),
      h('td', formatFans(row.fans)),
      h('td', row.competitor_status || '-'),
      h('td', [renderTruncatedText(row.task_name || row.task_id || '-', 'table-plain-text')]),
      h('td', row.profile_url || row.content_url ? h('a', { href: row.profile_url || row.content_url, target: '_blank', rel: 'noreferrer' }, '打开链接') : '-'),
      h('td', { class: 'row-actions' }, [
        h('button', { class: 'text-icon-button reserved', type: 'button', title: '采集竞品账号内容评论区寻找客户', onClick: () => emit('find-customers', row) }, [h(Search), h('span', '找客户')]),
        renderEnrichButton(library, row, emit, accountLibraries),
        h('button', { class: 'icon-button danger', title: '删除', onClick: () => emit('delete-row', library, row) }, [h(Delete)])
      ])
    ])
  }
  return h('tr', [
    h('td', [renderTablePrimaryCell(library, row)]),
    h('td', row.follow_status || row.competitor_status || row.status || '-'),
    h('td', [renderTruncatedText(row.task_name || row.task_id || '-', 'table-plain-text')]),
    h('td', row.profile_url || row.content_url ? h('a', { href: row.profile_url || row.content_url, target: '_blank', rel: 'noreferrer' }, '打开链接') : '-'),
    h('td', { class: 'row-actions' }, [
      ['competitor_candidates', 'lead_customers'].includes(library) ? h('button', { class: 'icon-button', title: 'AI分析', onClick: () => emit('analyze-row', library, row) }, [h(MagicStick)]) : null,
      renderEnrichButton(library, row, emit, accountLibraries),
      library === 'target_customers' && row.script ? h('button', { class: 'icon-button', title: '复制话术', onClick: () => navigator.clipboard.writeText(row.script) }, [h(CopyDocument)]) : null,
      h('button', { class: 'icon-button danger', title: '删除', onClick: () => emit('delete-row', library, row) }, [h(Delete)])
    ])
  ])
}

function renderEnrichButton(library: string, row: Dict, emit: any, accountLibraries: string[]) {
  if (!accountLibraries.includes(library)) return null
  return h('button', {
    class: ['icon-button', row.platform === 'ks' ? 'is-disabled' : ''],
    disabled: row.platform === 'ks',
    title: row.platform === 'ks' ? '快手主页资料暂不能从 SQLite 补回' : '补资料',
    onClick: () => emit('enrich-profile', library, row)
  }, [h(Refresh)])
}

function renderTruncatedText(value: unknown, className = 'table-muted-text') {
  const text = String(value || '-')
  return h('span', { class: className, title: text }, text)
}

function renderTablePrimaryCell(library: string, row: Dict) {
  const label = tablePrimaryText(row)
  const href = tablePrimaryHref(library, row)
  const attrs = { class: 'table-primary-text', title: label }
  if (!href) return h('strong', attrs, label)
  return h('a', { ...attrs, class: 'table-primary-link table-primary-text', href, target: '_blank', rel: 'noreferrer' }, label)
}

function tablePrimaryText(row: Dict) {
  return String(row.nickname || row.title || row.commenter_nickname || row.body || row.description || row.comment_samples || row.signature || row.id || '-')
}

function tablePrimaryHref(library: string, row: Dict) {
  if (library === 'contents') return row.content_url || row.author_url || ''
  if (library === 'comments') return row.content_url || row.commenter_url || ''
  return row.profile_url || row.content_url || row.commenter_url || row.author_url || ''
}

function formatFans(value: unknown) {
  if (value === null || value === undefined || value === '') return '-'
  const number = Number(value)
  if (!Number.isFinite(number)) return String(value)
  if (number >= 10000) return `${(number / 10000).toFixed(number >= 100000 ? 0 : 1)}万`
  return String(Math.trunc(number))
}
