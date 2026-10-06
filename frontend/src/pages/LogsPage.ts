import { defineComponent, h, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElDrawer } from 'element-plus'
import { Document, Tickets, Warning } from '@element-plus/icons-vue'
import type { Dict } from '../shared/types'
import { platformName, taskModeName } from '../shared/format'
import '../styles/operations.css'
import { ListPagination, type PageChange } from '../components/ui/ListPagination'
import { emptyState, sectionTitle } from '../components/ui/Workbench'

export default defineComponent({
  props: {
    tasks: { type: Array, required: true },
    selectedTask: { type: Object, default: null },
    diagnostics: { type: Object, default: () => ({}) },
    dedupSummary: { type: Object, default: () => ({}) },
    page: { type: Number, default: 1 },
    pageSize: { type: Number, default: 20 },
    total: { type: Number, default: 0 },
    query: { type: String, default: '' }
  },
  emits: ['select-task', 'retry-task', 'cancel-task', 'archive-task', 'delete-task', 'change-task-page', 'change-task-query'],
  setup(props, { emit }) {
    const taskSearch = ref(props.query)

    function applyTaskSearch() {
      emit('change-task-query', taskSearch.value)
    }

    watch(() => props.query, value => taskSearch.value = value)

    const route = useRoute()
    const router = useRouter()
    const detailOpen = ref(Boolean(route.query.task))
    const logDetailsOpen = ref(false)
    const logPage = ref(1)
    const logPageSize = ref(50)
    watch(() => props.selectedTask?.id, () => {
      logDetailsOpen.value = false
      logPage.value = 1
    })
    function openTask(id: string) {
      detailOpen.value = true
      emit('select-task', id)
      void router.replace({ path: '/acquisition', query: { ...route.query, task: id } })
    }
    watch(() => route.query.task, id => {
      detailOpen.value = Boolean(id)
      if (id && String(props.selectedTask?.id || '') !== String(id)) emit('select-task', String(id))
    }, { immediate: true })
    function closeTask() {
      detailOpen.value = false
      const { task, ...query } = route.query
      void router.replace({ path: '/acquisition', query })
    }
    return () => h('section', { class: 'panel acquisition-task-list' }, [
      sectionTitle({ title: '采集任务', subtitle: '查看产出后继续筛选与跟进', icon: Tickets, tone: 'blue' }),
      h('div', { class: 'toolbar task-list-tools' }, [
        h('input', { value: taskSearch.value, placeholder: '搜索任务ID或任务名', onInput: (event: Event) => taskSearch.value = (event.target as HTMLInputElement).value, onKeydown: (event: KeyboardEvent) => { if (event.key === 'Enter') applyTaskSearch() } }),
        h('button', { onClick: applyTaskSearch }, '搜索'), h('span', `共 ${props.total} 个任务`),
      ]),
      h('div', { class: 'operations-list' }, (props.tasks as Dict[]).length ? (props.tasks as Dict[]).map(task => h('article', { class: 'operations-list-row' }, [
        h('button', { class: 'operations-row-main', onClick: () => openTask(String(task.id)) }, [
          h('strong', task.name || task.id), h('span', `${platformName(task.platform)} · ${taskModeName(task.mode)} · ${task.keywords || task.creator_id || task.specified_id || ''}`),
          h('small', taskOutcomeSummary(task)),
        ]),
        h('span', { class: `status ${task.status}` }, taskStatusLabel(task.status)),
        h('div', { class: 'toolbar' }, [
          h('button', { onClick: () => openTask(String(task.id)) }, '查看结果'),
          ['queued', 'running', 'pending'].includes(task.status) ? h('button', { onClick: () => emit('cancel-task', task.id) }, '取消') : null,
          ['failed', 'cancelled', 'interrupted'].includes(task.status) ? h('button', { onClick: () => emit('retry-task', task) }, '修改参数重试') : null,
          !['queued', 'running', 'pending', 'failed'].includes(task.status) ? h('button', { onClick: () => emit('archive-task', task.id) }, '归档') : null,
          h('button', { class: 'danger', onClick: () => emit('delete-task', task.id) }, '删除'),
        ]),
      ])) : [emptyState({ title: '暂无匹配任务', description: '新建采集任务，或清空搜索条件。', icon: Tickets })]),
      h(ListPagination, { page: props.page, pageSize: props.pageSize, total: props.total, onChange: (payload: PageChange) => emit('change-task-page', payload) }),
      h(ElDrawer, { modelValue: detailOpen.value, title: props.selectedTask?.name || '任务详情', size: '840px', class: 'operations-detail', 'onUpdate:modelValue': (value: boolean) => { if (!value) closeTask() } }, () => props.selectedTask ? [
        h('p', { class: 'note' }, `${props.selectedTask.id} · ${taskStatusLabel(props.selectedTask.status)}`),
        renderTaskOutcome(props.selectedTask as Dict),
        h('div', { class: 'toolbar operations-next-actions' }, [
          Number(props.selectedTask.outcome?.counts?.competitor_candidates || 0) ? h('button', { class: 'primary-action', onClick: () => router.push({ path: '/ai', query: { tab: 'competitors', keyword: String(props.selectedTask?.keywords || '') } }) }, '筛选竞品账号') : null,
          Number(props.selectedTask.outcome?.counts?.leads || 0) ? h('button', { class: 'primary-action', onClick: () => router.push({ path: '/ai', query: { tab: 'leads', keyword: String(props.selectedTask?.keywords || '') } }) }, '分析客户意向') : null,
          Number(props.selectedTask.outcome?.counts?.target_customers || 0) ? h('button', { onClick: () => router.push('/message-workbench') }, '跟进目标客户') : null,
          h('button', { onClick: () => router.push('/overview') }, '查看来源证据'),
        ]),
        props.selectedTask.status === 'failed' ? renderDiagnostics(props.diagnostics as Dict) : h('details', [h('summary', '运行诊断'), renderDiagnostics(props.diagnostics as Dict)]),
        h('details', [h('summary', '防重复与删除记录'), renderDedupSummary(props.dedupSummary as Dict)]),
        h('details', { key: props.selectedTask.id, class: 'task-raw-logs', open: logDetailsOpen.value, onToggle: (event: Event) => logDetailsOpen.value = (event.target as HTMLDetailsElement).open }, [
          h('summary', `原始日志（${props.selectedTask.logs?.length || 0} 条）`),
          logDetailsOpen.value ? renderTaskLogs((props.selectedTask.logs || []).slice((logPage.value - 1) * logPageSize.value, logPage.value * logPageSize.value)) : null,
          logDetailsOpen.value ? h(ListPagination, { page: logPage.value, pageSize: logPageSize.value, total: props.selectedTask.logs?.length || 0, onChange: (payload: PageChange) => { logPage.value = payload.page; logPageSize.value = payload.page_size } }) : null,
        ]),
      ] : h('p', '正在加载任务详情…')),
    ])
  }
})

function renderTaskLogs(logs: Dict[]) {
  // 原始日志按需展开，复用可滚动日志区域，不挤占任务主列表。
  return logs.length ? h('div', { class: 'traffic-log-list' }, logs.map(log => h('div', { key: log.id, class: ['traffic-log-item', `log-${log.level}`] }, [
    h('time', log.created_at || ''),
    h('div', { class: 'traffic-log-body' }, [h('small', log.level || 'info'), h('pre', String(log.message || ''))]),
  ]))) : h('p', { class: 'muted-text' }, '暂无运行日志')
}

function renderTaskOutcome(task: Dict) {
  const outcome = task.outcome || {}
  const counts = outcome.counts || {}
  const actions = outcome.next_actions || []
  const metrics = [
    ['内容', counts.contents || 0],
    ['评论', counts.comments || 0],
    ['候选竞品', counts.competitor_candidates || 0],
    ['线索', counts.leads || 0],
    ['目标客户', counts.target_customers || 0],
    ['需补资料', counts.profile_enrichment_needed || 0]
  ]
  return h('div', { class: 'task-outcome' }, [
    h('div', { class: 'task-outcome-head' }, [
      h('strong', '任务产出'),
      h('span', { class: `outcome-health ${outcome.health || 'pending'}` }, outcomeHealthLabel(outcome.health))
    ]),
    h('div', { class: 'task-outcome-grid' }, metrics.map(([label, value]) => h('div', [
      h('small', String(label)),
      h('strong', String(value))
    ]))),
    actions.length
      ? h('ul', { class: 'task-next-actions' }, actions.map((action: string) => h('li', action)))
      : h('p', { class: 'task-next-actions empty' }, '暂无下一步动作，等待任务完成或查看日志')
  ])
}

function renderDiagnostics(diagnostic: Dict) {
  if (!diagnostic || !Object.keys(diagnostic).length) {
    return h('div', { class: 'diagnostic-panel task-diagnostic' }, [
      h('div', { class: 'task-outcome-head' }, [h('strong', '失败诊断'), h('span', '等待数据')]),
      h('p', '正在加载任务诊断信息')
    ])
  }
  const evidence = Array.isArray(diagnostic.evidence) ? diagnostic.evidence : []
  const steps = Array.isArray(diagnostic.next_steps) ? diagnostic.next_steps : []
  return h('div', { class: ['diagnostic-panel', 'task-diagnostic', `diagnostic-${diagnostic.status || 'ok'}`] }, [
    h('div', { class: 'task-outcome-head' }, [
      h('strong', '失败诊断'),
      h('span', `${diagnostic.category || '未知'} · ${diagnostic.retryable ? '建议可重试' : '不建议直接重试'}`)
    ]),
    h('p', diagnostic.summary || '暂无诊断结论'),
    evidence.length ? h('div', { class: 'diagnostic-evidence' }, [
      h('strong', '证据'),
      ...evidence.slice(0, 4).map((item: Dict) => h('pre', item.counts ? JSON.stringify(item.counts, null, 2) : String(item.message || '')))
    ]) : null,
    steps.length ? h('ul', { class: 'task-next-actions' }, steps.map((step: string) => h('li', step))) : null
  ])
}

function renderDedupSummary(summary: Dict) {
  if (!summary || !Object.keys(summary).length) return null
  const imported = summary.imported_counts || {}
  const tombstones = Array.isArray(summary.tombstone_counts) ? summary.tombstone_counts : []
  const audit = Array.isArray(summary.audit) ? summary.audit : []
  const notes = Array.isArray(summary.notes) ? summary.notes : []
  return h('div', { class: 'diagnostic-panel task-dedup' }, [
    h('div', { class: 'task-outcome-head' }, [h('strong', '防重复与删除记录'), h('span', `底层映射 ${summary.raw_refs || 0}`)]),
    h('div', { class: 'task-outcome-grid compact-grid' }, [
      renderDedupMetric('内容', imported.contents || 0),
      renderDedupMetric('评论', imported.comments || 0),
      renderDedupMetric('候选竞品', imported.competitor_candidates || 0),
      renderDedupMetric('线索', imported.leads || 0)
    ]),
    tombstones.length ? h('div', { class: 'dedup-inline-list' }, [
      h('strong', '相关墓碑'),
      ...tombstones.map((item: Dict) => h('span', `${entityTypeLabel(item.entity_type)} ${item.count || 0}`))
    ]) : h('p', { class: 'muted-text' }, '暂未找到与该任务直接关联的墓碑记录'),
    audit.length ? h('div', { class: 'dedup-audit-list' }, [
      h('strong', '删除审计'),
      ...audit.slice(0, 5).map((item: Dict) => h('small', `${item.created_at || ''} ${item.entity_type || ''} ${item.hard_delete ? '硬删除' : '记录删除'}`))
    ]) : null,
    notes.length ? h('ul', { class: 'task-next-actions' }, notes.map((note: string) => h('li', note))) : null
  ])
}

function renderDedupMetric(label: string, value: number) {
  return h('div', [h('small', label), h('strong', String(value))])
}

function taskOutcomeSummary(task: Dict) {
  const counts = task.outcome?.counts || {}
  return `已入库：内容 ${counts.contents || 0} / 评论 ${counts.comments || 0} / 候选竞品 ${counts.competitor_candidates || 0} / 线索 ${counts.leads || 0}`
}

function outcomeHealthLabel(health: string) {
  return ({
    actionable: '可处理',
    collected: '已采集',
    empty: '无有效数据',
    failed: '失败',
    pending: '等待结果'
  } as Record<string, string>)[health] || '等待结果'
}

function entityTypeLabel(type: string) {
  return ({ author_account: '账号', content: '内容', comment: '评论' } as Record<string, string>)[type] || type || '-'
}

function taskStatusLabel(status: string) {
  return ({ pending: '待执行', queued: '排队中', running: '采集中', succeeded: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断', archived: '已归档' } as Record<string, string>)[status] || status
}
