import { computed, defineComponent, h, nextTick, ref, watch } from 'vue'
import type { Dict } from '../shared/types'
import { clamp, platformName } from '../shared/format'
import { SplitPane } from '../components/ui/SplitPane'

export default defineComponent({
  props: {
    tasks: { type: Array, required: true },
    selectedTask: { type: Object, default: null },
    diagnostics: { type: Object, default: () => ({}) },
    dedupSummary: { type: Object, default: () => ({}) }
  },
  emits: ['select-task', 'retry-task', 'cancel-task', 'archive-task', 'delete-task'],
  setup(props, { emit }) {
    const taskSearch = ref('')
    const taskPage = ref(1)
    const taskPageSize = 5
    const logConsoleRef = ref<HTMLElement | null>(null)

    const filteredTasks = computed(() => {
      const keyword = taskSearch.value.trim().toLowerCase()
      const tasks = props.tasks as Dict[]
      if (!keyword) return tasks
      return tasks.filter(task => {
        const taskId = String(task.id || '').toLowerCase()
        const taskName = String(task.name || '').toLowerCase()
        return taskId.includes(keyword) || taskName.includes(keyword)
      })
    })

    const totalTaskPages = computed(() => Math.max(1, Math.ceil(filteredTasks.value.length / taskPageSize)))
    const pagedTasks = computed(() => {
      const start = (taskPage.value - 1) * taskPageSize
      return filteredTasks.value.slice(start, start + taskPageSize)
    })

    function goTaskPage(delta: number) {
      taskPage.value = clamp(taskPage.value + delta, 1, totalTaskPages.value)
    }

    function scrollLogToBottom() {
      nextTick(() => {
        const logConsole = logConsoleRef.value
        if (logConsole) logConsole.scrollTop = logConsole.scrollHeight
      })
    }

    watch(() => [taskSearch.value, (props.tasks as Dict[]).length], () => {
      taskPage.value = 1
    })

    watch(totalTaskPages, pages => {
      if (taskPage.value > pages) taskPage.value = pages
    })

    watch(() => [props.selectedTask?.id, (props.selectedTask?.logs || []).length], scrollLogToBottom, { immediate: true })

    return () => h(SplitPane, { storageKey: 'logs', side: 'right', defaultSideWidth: 390 }, {
      default: () => [
      h('section', { class: 'pane primary-pane log-pane' }, [
        h('div', { class: 'section-title' }, [h('h2', props.selectedTask ? `${props.selectedTask.id} · ${props.selectedTask.name}` : '任务详情'), h('span', props.selectedTask?.status || '请选择任务')]),
        props.selectedTask ? renderTaskOutcome(props.selectedTask as Dict) : h('div', { class: 'empty-state compact' }, '请选择左侧任务查看产出和日志'),
        props.selectedTask ? renderDiagnostics(props.diagnostics as Dict) : null,
        props.selectedTask ? renderDedupSummary(props.dedupSummary as Dict) : null,
        h('div', { class: 'log-console', ref: logConsoleRef }, (props.selectedTask?.logs || []).map((log: Dict) => h('p', [h('time', log.created_at), h('span', log.message)])))
      ])
      ],
      side: () => [
      h('aside', { class: 'pane side-pane' }, [
        h('div', { class: 'section-title' }, [h('h2', '任务列表'), h('span', '归档前先看日志')]),
        h('div', { class: 'task-list-tools' }, [
          h('input', {
            value: taskSearch.value,
            placeholder: '搜索任务ID或任务名',
            onInput: (event: Event) => {
              taskSearch.value = (event.target as HTMLInputElement).value
            }
          }),
          h('small', `共 ${filteredTasks.value.length} 个任务`)
        ]),
        h('div', { class: 'task-list' }, pagedTasks.value.length ? pagedTasks.value.map(task => h('article', {
          class: ['task-row', { selected: props.selectedTask?.id === task.id }],
          role: 'button',
          tabindex: 0,
          onClick: () => emit('select-task', task.id),
          onKeydown: (event: KeyboardEvent) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault()
              emit('select-task', task.id)
            }
          }
        }, [
          h('div', { class: 'task-row-head' }, [
            h('div', { class: 'task-title' }, [
              h('span', { class: 'task-id-label' }, `任务ID ${task.id}`),
              h('strong', task.name || '-')
            ]),
            h('em', { class: `status ${task.status}` }, task.status)
          ]),
          h('div', { class: 'task-params' }, taskParameterItems(task).map(item => h('span', { title: item.value }, [
            h('small', item.label),
            h('b', item.value)
          ]))),
          h('span', { class: 'task-row-outcome' }, taskOutcomeSummary(task)),
          h('div', { class: 'task-card-actions' }, [
            task.status === 'running' ? h('button', {
              class: 'warning-soft',
              onClick: (event: MouseEvent) => {
                event.stopPropagation()
                emit('cancel-task', task.id)
              }
            }, '取消') : null,
            task.status === 'failed' ? h('button', {
              class: 'primary-soft',
              onClick: (event: MouseEvent) => {
                event.stopPropagation()
                emit('retry-task', task)
              }
            }, '重试') : null,
            !['failed', 'running'].includes(task.status) ? h('button', {
              onClick: (event: MouseEvent) => {
                event.stopPropagation()
                emit('archive-task', task.id)
              }
            }, '归档') : null,
            h('button', {
              class: 'danger',
              onClick: (event: MouseEvent) => {
                event.stopPropagation()
                emit('delete-task', task.id)
              }
            }, '删除')
          ])
        ])) : [h('div', { class: 'empty-state compact' }, '没有匹配任务')]),
        h('div', { class: 'task-list-pagination' }, [
          h('button', { disabled: taskPage.value <= 1, onClick: () => goTaskPage(-1) }, '上一页'),
          h('span', `${taskPage.value} / ${totalTaskPages.value}`),
          h('button', { disabled: taskPage.value >= totalTaskPages.value, onClick: () => goTaskPage(1) }, '下一页')
        ])
      ])
      ]
    })
  }
})

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

function taskParameterItems(task: Dict) {
  const items = [
    { label: '平台', value: platformName(String(task.platform || '-')) }
  ]
  if (task.keywords) items.push({ label: '关键词', value: String(task.keywords) })
  if (task.creator_id) items.push({ label: '主页/ID', value: String(task.creator_id) })
  if (task.specified_id) items.push({ label: '内容ID/链接', value: String(task.specified_id) })
  if (!task.keywords && !task.creator_id && !task.specified_id) items.push({ label: '采集对象', value: task.mode || '-' })
  return items
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
