import { computed, defineComponent, h, nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { Close, Refresh, Tickets, VideoCamera, Warning } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import { SplitPane } from '../components/ui/SplitPane'
import { emptyState, sectionTitle } from '../components/ui/Workbench'
import { api } from '../shared/api'
import { clamp } from '../shared/format'
import type { Dict } from '../shared/types'

export default defineComponent({
  name: 'TrafficLogsPage',
  setup() {
    const loading = ref(false)
    const runs = ref<Dict[]>([])
    const selectedRunId = ref('')
    const selectedRun = ref<Dict | null>(null)
    const targets = ref<Dict>({ rows: [], total: 0, page: 1, page_size: 30 })
    const runSearch = ref('')
    const runPage = ref(1)
    const logConsoleRef = ref<HTMLElement | null>(null)
    const logAutoFollow = ref(true)
    const runPageSize = 6
    const LOG_BOTTOM_THRESHOLD = 28
    let refreshTimer: ReturnType<typeof window.setInterval> | null = null

    const filteredRuns = computed(() => {
      const keyword = runSearch.value.trim().toLowerCase()
      if (!keyword) return runs.value
      return runs.value.filter(run => {
        const runId = String(run.id || '').toLowerCase()
        const campaignName = String(run.campaign_name || '').toLowerCase()
        const status = String(run.status || '').toLowerCase()
        return runId.includes(keyword) || campaignName.includes(keyword) || status.includes(keyword)
      })
    })
    const totalRunPages = computed(() => Math.max(1, Math.ceil(filteredRuns.value.length / runPageSize)))
    const pagedRuns = computed(() => {
      const start = (runPage.value - 1) * runPageSize
      return filteredRuns.value.slice(start, start + runPageSize)
    })

    function isLogNearBottom(logConsole: HTMLElement) {
      return logConsole.scrollHeight - logConsole.scrollTop - logConsole.clientHeight <= LOG_BOTTOM_THRESHOLD
    }

    function updateLogFollowState() {
      const logConsole = logConsoleRef.value
      if (!logConsole) return
      logAutoFollow.value = isLogNearBottom(logConsole)
    }

    function scrollLogToBottom(force = false) {
      nextTick(() => {
        const logConsole = logConsoleRef.value
        if (!logConsole) return
        if (!force && !logAutoFollow.value) return
        logConsole.scrollTop = logConsole.scrollHeight
        logAutoFollow.value = true
      })
    }

    async function loadRuns() {
      const { data } = await api.get('/traffic/runs')
      runs.value = Array.isArray(data) ? data : []
      if (!runs.value.some(run => String(run.id || '') === selectedRunId.value)) {
        selectedRunId.value = String(runs.value[0]?.id || '')
      }
      if (!selectedRunId.value) {
        selectedRun.value = null
        targets.value = { rows: [], total: 0, page: 1, page_size: 30 }
      }
    }

    async function loadSelectedRun(page = Number(targets.value.page || 1)) {
      if (!selectedRunId.value) return
      const [runResponse, targetsResponse] = await Promise.all([
        api.get(`/traffic/runs/${selectedRunId.value}`),
        api.get(`/traffic/runs/${selectedRunId.value}/targets`, { params: { page, page_size: 30 } }),
      ])
      selectedRun.value = runResponse.data
      targets.value = targetsResponse.data
    }

    async function loadAll(silent = false) {
      if (loading.value && silent) return
      if (!silent) loading.value = true
      try {
        await loadRuns()
        await loadSelectedRun()
      } catch (error: any) {
        if (!silent) ElMessage.error(error?.response?.data?.detail || '加载引流批次失败')
      } finally {
        if (!silent) loading.value = false
      }
    }

    async function selectRun(runId: string) {
      selectedRunId.value = runId
      logAutoFollow.value = true
      await loadSelectedRun(1)
      scrollLogToBottom(true)
    }

    async function cancelRun(run: Dict) {
      try {
        await api.post(`/traffic/runs/${run.id}/cancel`)
        ElMessage.success('已请求停止引流批次')
        await loadAll(true)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '停止引流批次失败')
      }
    }

    function goRunPage(delta: number) {
      runPage.value = clamp(runPage.value + delta, 1, totalRunPages.value)
    }

    watch(() => [runSearch.value, runs.value.length], () => {
      runPage.value = 1
    })

    watch(totalRunPages, pages => {
      if (runPage.value > pages) runPage.value = pages
    })

    watch(
      () => ({
        runId: String(selectedRun.value?.id || ''),
        eventCount: (selectedRun.value?.events || []).length,
      }),
      (current, previous) => {
        if (!current.runId) return
        const runChanged = !previous || current.runId !== previous.runId
        if (runChanged) {
          logAutoFollow.value = true
          scrollLogToBottom(true)
          return
        }
        if (!previous || current.eventCount > previous.eventCount) scrollLogToBottom(false)
      },
      { immediate: true }
    )

    onMounted(() => {
      void loadAll()
      // 日志页独立轮询当前批次，避免依赖应用壳只刷新 dashboard。
      refreshTimer = window.setInterval(() => {
        void loadAll(true)
      }, 3000)
    })

    onBeforeUnmount(() => {
      if (!refreshTimer) return
      window.clearInterval(refreshTimer)
      refreshTimer = null
    })

    return () => h(SplitPane, { storageKey: 'traffic-logs', side: 'right', defaultSideWidth: 390 }, {
      default: () => [
        h('section', { class: 'pane primary-pane log-pane traffic-main-pane' }, [
          sectionTitle({
            title: selectedRun.value ? `${selectedRun.value.id} · ${selectedRun.value.campaign_name || '引流批次'}` : '引流批次详情',
            subtitle: selectedRun.value ? statusLabel(String(selectedRun.value.status || '')) : '请选择批次',
            icon: selectedRun.value?.status === 'failed' ? Warning : Tickets,
            tone: selectedRun.value?.status === 'failed' ? 'red' : 'teal',
            aside: h('button', { class: 'secondary-action', disabled: loading.value, onClick: () => loadAll() }, [h(Refresh, { class: 'inline-icon' }), '刷新']),
          }),
          selectedRun.value
            ? [
                renderRunOutcome(selectedRun.value),
                renderEventConsole(selectedRun.value, logConsoleRef, updateLogFollowState),
                renderRunTargets(targets.value, loadSelectedRun),
              ]
            : emptyState({
                title: '暂无引流批次',
                description: '从定向引流或随机引流页面启动批次后，这里会显示批次状态、执行日志和已处理视频。',
                icon: Tickets,
                tone: 'gray',
              }),
        ]),
      ],
      side: () => [
        h('aside', { class: 'pane side-pane' }, [
          sectionTitle({ title: '批次列表', subtitle: '选择一个批次查看日志', icon: Tickets, tone: 'blue' }),
          h('div', { class: 'task-list-tools' }, [
            h('input', {
              value: runSearch.value,
              placeholder: '搜索批次ID、计划名或状态',
              onInput: (event: Event) => {
                runSearch.value = (event.target as HTMLInputElement).value
              },
            }),
            h('small', `共 ${filteredRuns.value.length} 个批次`),
          ]),
          h('div', { class: 'task-list' }, pagedRuns.value.length
            ? pagedRuns.value.map(run => renderRunRow(run, selectedRunId.value, selectRun, cancelRun))
            : [emptyState({
                title: '没有匹配批次',
                description: '清空搜索词，或先从定向引流、随机引流页面启动新的引流批次。',
                icon: Tickets,
                tone: 'gray',
              })]),
          h('div', { class: 'task-list-pagination' }, [
            h('button', { disabled: runPage.value <= 1, onClick: () => goRunPage(-1) }, '上一页'),
            h('span', `${runPage.value} / ${totalRunPages.value}`),
            h('button', { disabled: runPage.value >= totalRunPages.value, onClick: () => goRunPage(1) }, '下一页'),
          ]),
        ]),
      ],
    })
  },
})

function renderRunOutcome(run: Dict) {
  const counts = run.counts || {}
  const metrics = [
    ['成功', counts.succeeded || 0],
    ['失败', counts.failed || 0],
    ['跳过', counts.skipped || 0],
    ['每轮上限', run.per_run_limit || 0],
    ['每日上限', run.daily_limit || 0],
    ['进程', run.process_id || '-'],
  ]
  return h('div', { class: 'task-outcome' }, [
    h('div', { class: 'task-outcome-head' }, [
      h('strong', '批次摘要'),
      h('span', { class: ['traffic-status-pill', `status-${run.status || 'pending'}`] }, statusLabel(String(run.status || ''))),
    ]),
    h('div', { class: 'task-outcome-grid compact-grid' }, metrics.map(([label, value]) => h('div', [
      h('small', String(label)),
      h('strong', String(value)),
    ]))),
    run.error ? h('p', { class: 'muted-text' }, `失败原因：${run.error}`) : null,
  ])
}

function renderEventConsole(run: Dict, logConsoleRef: Ref<HTMLElement | null>, onScroll: () => void) {
  const events = Array.isArray(run.events) ? [...run.events].reverse() : []
  return h('div', { class: 'log-console', ref: logConsoleRef, onScroll }, events.length
    ? events.map((event: Dict) => h('p', [
        h('time', String(event.created_at || '')),
        h('span', `${eventStatusLabel(String(event.status || ''))} · ${actionLabel(String(event.action || ''))}${event.detail ? ` · ${event.detail}` : ''}`),
      ]))
    : [h('p', [h('span', '暂无日志输出')])])
}

function renderRunTargets(targets: Dict, loadTargets: (page?: number) => Promise<void>) {
  const rows = Array.isArray(targets.rows) ? targets.rows : []
  const page = Number(targets.page || 1)
  const pageSize = Number(targets.page_size || 30)
  const total = Number(targets.total || 0)
  const pageEnd = Math.min(page * pageSize, total)
  const hasNext = pageEnd < total
  return h('div', { class: 'table-content traffic-target-content' }, [
    sectionTitle({ title: '批次目标', subtitle: `共 ${total} 条`, icon: VideoCamera, tone: 'purple', compact: true }),
    h('div', { class: 'table-scroll' }, [
      h('table', { class: 'data-table traffic-target-data-table' }, [
        h('thead', [h('tr', [
          h('th', '视频'),
          h('th', '作者/来源'),
          h('th', '发送内容'),
          h('th', '状态'),
          h('th', '最后时间'),
          h('th', '失败原因'),
        ])]),
        h('tbody', rows.length ? rows.map((row: Dict) => h('tr', [
          h('td', [renderTargetTitle(row)]),
          h('td', [h('span', { class: 'table-muted-text' }, row.author_name || row.keyword || row.source_type || '-')]),
          h('td', [h('span', { class: 'table-muted-text' }, row.selected_comment || '执行时随机选择')]),
          h('td', [h('span', { class: ['traffic-status-pill', `status-${row.status || 'pending'}`] }, statusLabel(String(row.status || 'pending')))]),
          h('td', [h('span', { class: 'table-muted-text' }, row.last_action_at || row.updated_at || '-')]),
          h('td', [h('span', { class: 'table-muted-text' }, row.error || '-')]),
        ])) : [
          h('tr', [h('td', { class: 'table-empty', colspan: 6 }, '该批次暂无目标明细；随机引流和搜索关键词引流会在执行过程中写入。')]),
        ]),
      ]),
    ]),
    h('div', { class: 'table-pagination' }, [
      h('span', total ? `显示 ${(page - 1) * pageSize + 1}-${pageEnd} / ${total}` : '0 条记录'),
      h('div', { class: 'table-page-controls' }, [
        h('button', { type: 'button', disabled: page <= 1, onClick: () => loadTargets(page - 1) }, '上一页'),
        h('span', `${page} / ${Math.max(1, Math.ceil(total / pageSize))}`),
        h('button', { type: 'button', disabled: !hasNext, onClick: () => loadTargets(page + 1) }, '下一页'),
      ]),
    ]),
  ])
}

function renderRunRow(run: Dict, selectedRunId: string, selectRun: (runId: string) => Promise<void>, cancelRun: (run: Dict) => Promise<void>) {
  const runId = String(run.id || '')
  return h('article', {
    class: ['task-row', { selected: selectedRunId === runId }],
    role: 'button',
    tabindex: 0,
    onClick: () => selectRun(runId),
    onKeydown: (event: KeyboardEvent) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        void selectRun(runId)
      }
    },
  }, [
    h('div', { class: 'task-row-head' }, [
      h('div', { class: 'task-title' }, [
        h('span', { class: 'task-id-label' }, `批次ID ${runId}`),
        h('strong', run.campaign_name || '引流批次'),
      ]),
      h('em', { class: ['status', String(run.status || 'pending')] }, statusLabel(String(run.status || 'pending'))),
    ]),
    h('div', { class: 'task-params' }, runParameterItems(run).map(item => h('span', { title: item.value }, [
      h('small', item.label),
      h('b', item.value),
    ]))),
    h('span', { class: 'task-row-outcome' }, runOutcomeSummary(run)),
    isActiveRun(String(run.status || '')) ? h('div', { class: 'task-card-actions' }, [
      h('button', {
        class: 'warning-soft',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          void cancelRun(run)
        },
      }, [h(Close, { class: 'inline-icon' }), '停止']),
    ]) : null,
  ])
}

function renderTargetTitle(row: Dict) {
  const label = row.title || row.content_url || `视频 ${row.id}`
  const attrs = { class: 'table-primary-text', title: label }
  return row.content_url
    ? h('a', { ...attrs, class: 'table-primary-link table-primary-text', href: row.content_url, target: '_blank', rel: 'noreferrer' }, label)
    : h('span', attrs, label)
}

function runParameterItems(run: Dict) {
  return [
    { label: '计划ID', value: String(run.campaign_id || '-') },
    { label: '上限', value: `${run.per_run_limit || 0} / 日 ${run.daily_limit || 0}` },
    { label: '开始', value: String(run.started_at || run.created_at || '-') },
    { label: '结束', value: String(run.finished_at || '-') },
  ]
}

function runOutcomeSummary(run: Dict) {
  const counts = run.counts || {}
  return `成功 ${counts.succeeded || 0} / 失败 ${counts.failed || 0} / 跳过 ${counts.skipped || 0}${run.error ? ` · ${run.error}` : ''}`
}

function isActiveRun(status: string) {
  return ['pending', 'running'].includes(status)
}

function statusLabel(status: string) {
  return ({
    pending: '待执行',
    running: '运行中',
    succeeded: '已完成',
    failed: '失败',
    skipped: '已跳过',
    cancelled: '已停止',
  } as Record<string, string>)[status] || status || '-'
}

function eventStatusLabel(status: string) {
  return ({
    info: '信息',
    warning: '警告',
    error: '错误',
    succeeded: '成功',
    failed: '失败',
    skipped: '跳过',
  } as Record<string, string>)[status] || status || '-'
}

function actionLabel(action: string) {
  return ({
    system: '系统',
    open: '打开视频',
    like: '点赞',
    follow: '关注',
    comment: '评论',
    upload_image: '发送图片',
    rule: '规则判断',
  } as Record<string, string>)[action] || action || '-'
}
