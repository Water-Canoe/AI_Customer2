import { computed, defineComponent, h, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { DataLine, MagicStick, Message, Promotion, Refresh, Search, Tickets, VideoPlay } from '@element-plus/icons-vue'

import { api } from '../shared/api'
import type { Dict } from '../shared/types'
import { SplitPane } from '../components/ui/SplitPane'
import { emptyState, metricTile, pageAction, sectionTitle } from '../components/ui/Workbench'

const leadExamples = [
  '检查下我现在有多少竞品账户和目标客户',
  '我现在还有多少用户未私信',
  '为我找些做跨境电商AI客服的竞品',
  '为我找些对AI客服有需求的客户，先不私信',
  '将还未私信的客户私信',
]

const trafficExamples = [
  '检查最近的引流执行情况',
  '帮我围绕AI客服做一轮抖音点赞引流',
  '找跨境电商相关视频，点赞并关注，每轮处理10个',
]

export default defineComponent({
  name: 'AutoAgentPage',
  setup() {
    const route = useRoute()
    const router = useRouter()
    const command = ref('')
    const preview = ref<Dict | null>(null)
    const runs = ref<Dict[]>([])
    const selectedRun = ref<Dict | null>(null)
    const events = ref<Dict[]>([])
    const loading = ref(false)
    const thinking = ref(false)
    const executing = ref(false)
    let timer: ReturnType<typeof window.setInterval> | null = null

    const runType = computed(() => route.name === 'traffic-auto' ? 'traffic_auto' : 'lead_auto')
    const workspace = computed(() => route.name === 'traffic-auto' ? 'traffic' : 'lead')
    const isTraffic = computed(() => runType.value === 'traffic_auto')
    const title = computed(() => isTraffic.value ? 'AI自动引流' : 'AI自动拓客')
    const examples = computed(() => isTraffic.value ? trafficExamples : leadExamples)

    onMounted(() => {
      loadRunsAndEvents()
      // 轮询只刷新批次、日志和结果，指令解析仍由用户主动触发。
      timer = window.setInterval(loadRunsAndEvents, 3000)
    })
    onBeforeUnmount(() => {
      if (timer) window.clearInterval(timer)
    })
    watch(runType, () => {
      command.value = ''
      preview.value = null
      selectedRun.value = null
      events.value = []
      loadRunsAndEvents()
    })

    async function loadRunsAndEvents() {
      loading.value = true
      try {
        const { data } = await api.get('/agent/runs', { params: { run_type: runType.value } })
        runs.value = data
        const selectedId = selectedRun.value?.id
        const next = selectedId ? data.find((item: Dict) => item.id === selectedId) : data[0]
        if (next) await selectRun(next.id)
        else {
          selectedRun.value = null
          events.value = []
        }
      } finally {
        loading.value = false
      }
    }

    async function selectRun(id: string) {
      const [detail, eventResult] = await Promise.all([
        api.get(`/agent/runs/${id}`),
        api.get(`/agent/runs/${id}/events`),
      ])
      selectedRun.value = detail.data
      events.value = eventResult.data
    }

    async function previewCommand() {
      const text = command.value.trim()
      if (!text) {
        ElMessage.warning('先输入你想让AI做什么')
        return
      }
      thinking.value = true
      try {
        const { data } = await api.post('/agent/commands/preview', { command: text, workspace: workspace.value })
        preview.value = data
        if (data.executed) ElMessage.success('AI已完成查询')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || 'AI指令解析失败')
      } finally {
        thinking.value = false
      }
    }

    async function executePreview() {
      const text = command.value.trim()
      if (!text || !preview.value?.plan) return
      executing.value = true
      try {
        const { data } = await api.post('/agent/commands/execute', {
          command: text,
          workspace: workspace.value,
          plan: preview.value.plan,
        })
        preview.value = data
        const runId = data.run?.id || data.result?.run?.id
        if (runId) {
          await loadRunsAndEvents()
          await selectRun(String(runId))
        }
        ElMessage.success(data.answer || 'AI自动化批次已创建')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || 'AI执行失败')
      } finally {
        executing.value = false
      }
    }

    async function cancelSelected() {
      if (!selectedRun.value?.id) return
      try {
        const { data } = await api.post(`/agent/runs/${selectedRun.value.id}/cancel`)
        selectedRun.value = data
        await loadRunsAndEvents()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '停止失败')
      }
    }

    return () => h(SplitPane, { storageKey: `auto-agent-${runType.value}`, side: 'right', defaultSideWidth: 430 }, {
      default: () => h('section', { class: 'pane primary-pane auto-agent-pane' }, [
        pageAction({
          title: title.value,
          description: isTraffic.value
            ? '输入一句话，AI会判断是查询引流数据，还是创建并启动抖音引流批次。'
            : '输入一句话，AI会判断是查询数据、找竞品、找客户，还是私信未触达客户。',
          icon: isTraffic.value ? Promotion : MagicStick,
          tone: isTraffic.value ? 'blue' : 'teal',
        }),
        renderCommandBox(command.value, examples.value, thinking.value, previewCommand, value => { command.value = value }),
        renderPreview(preview.value, executing.value, executePreview),
      ]),
      side: () => h('aside', { class: 'pane side-pane auto-agent-side' }, [
        renderRunList(runs.value, selectedRun.value, selectRun, cancelSelected),
        renderEvents(events.value),
        renderResult(selectedRun.value, isTraffic.value, router),
      ]),
    })
  },
})

function renderCommandBox(
  command: string,
  examples: string[],
  thinking: boolean,
  submit: () => void,
  update: (value: string) => void,
) {
  return h('section', { class: 'auto-agent-section' }, [
    sectionTitle({ title: '我要做什么', subtitle: '直接输入自然语言指令', icon: Search, tone: 'teal' }),
    h('label', { class: 'auto-agent-field auto-agent-field-wide' }, [
      h('span', '指令'),
      h('textarea', {
        value: command,
        placeholder: '例如：为我找些对AI客服有需求的客户，先不私信',
        onInput: (event: Event) => update((event.target as HTMLTextAreaElement).value),
        onKeydown: (event: KeyboardEvent) => {
          if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') submit()
        },
      }),
    ]),
    h('div', { class: 'auto-agent-examples' }, examples.map(item => h('button', {
      type: 'button',
      class: 'secondary-action compact-action',
      onClick: () => update(item),
    }, item))),
    h('div', { class: 'auto-agent-submit' }, [
      h('button', { class: 'primary-action', disabled: thinking, onClick: submit }, [
        h(MagicStick, { class: 'inline-icon' }),
        thinking ? '理解中' : '让AI处理',
      ]),
    ]),
  ])
}

function renderPreview(preview: Dict | null, executing: boolean, execute: () => void) {
  if (!preview) {
    return h('section', { class: 'auto-agent-section' }, [
      sectionTitle({ title: 'AI计划', subtitle: '查询会直接返回结果，执行会先预览计划', icon: MagicStick, tone: 'blue', compact: true }),
      emptyState({ title: '等待指令', description: '输入一句话后，AI会决定查询数据或创建自动化批次。', icon: MagicStick, tone: 'gray' }),
    ])
  }
  const plan = preview.plan || {}
  return h('section', { class: 'auto-agent-section' }, [
    sectionTitle({
      title: preview.executed ? 'AI答复' : 'AI计划',
      subtitle: actionLabel(String(plan.action || 'unknown')),
      icon: preview.executed ? DataLine : MagicStick,
      tone: preview.executed ? 'green' : 'blue',
      compact: true,
    }),
    h('div', { class: 'auto-agent-plan' }, [
      h('strong', plan.title || 'AI计划'),
      preview.answer ? h('p', preview.answer) : null,
      renderPlanMeta(plan),
      Array.isArray(plan.steps) && plan.steps.length
        ? h('ol', { class: 'auto-agent-steps' }, plan.steps.map((step: string) => h('li', step)))
        : null,
      preview.requires_confirmation
        ? h('div', { class: 'auto-agent-submit' }, [
            h('button', { class: 'primary-action', disabled: executing, onClick: execute }, [
              h(VideoPlay, { class: 'inline-icon' }),
              executing ? '创建中' : '确认执行',
            ]),
          ])
        : null,
    ]),
    renderQueryResult(preview.result || {}),
  ])
}

function renderPlanMeta(plan: Dict) {
  const items: Array<[string, string]> = [['动作', actionLabel(String(plan.action || 'unknown'))]]
  if (Array.isArray(plan.keywords) && plan.keywords.length) items.push(['关键词', plan.keywords.join('、')])
  if (plan.lead_operation) items.push(['拓客方式', leadOperationLabel(String(plan.lead_operation))])
  if (plan.auto_dm !== undefined) items.push(['自动私信', plan.auto_dm ? '是' : '否'])
  if (plan.dm_count) items.push(['私信数量', String(plan.dm_count)])
  if (plan.source_mode) items.push(['来源模式', sourceModeLabel(String(plan.source_mode))])
  if (plan.source_value) items.push(['来源值', String(plan.source_value)])
  if (Array.isArray(plan.actions) && plan.actions.length) items.push(['动作组合', plan.actions.map(actionName).join('、')])
  if (plan.round_video_limit) items.push(['每轮视频', String(plan.round_video_limit)])
  return h('div', { class: 'auto-agent-plan-meta' }, items.map(([label, value]) => h('span', [
    h('b', `${label}：`),
    value,
  ])))
}

function renderQueryResult(result: Dict) {
  const rows = Array.isArray(result.rows) ? result.rows : []
  if (!rows.length && !result.values) return null
  if (result.values) {
    return h('div', { class: 'auto-agent-metrics' }, [
      metricTile({ label: '竞品账户', value: result.values.competitors || 0, icon: MagicStick, tone: 'blue' }),
      metricTile({ label: '目标客户', value: result.values.target_customers || 0, icon: Message, tone: 'green' }),
      metricTile({ label: '未私信', value: result.values.unmessaged || 0, icon: Message, tone: 'teal' }),
      metricTile({ label: '私信成功', value: result.values.dm_success || 0, icon: Tickets, tone: 'purple' }),
    ])
  }
  const columns = Array.isArray(result.columns) && result.columns.length ? result.columns.slice(0, 6) : Object.keys(rows[0] || {}).slice(0, 6)
  return h('div', { class: 'auto-agent-table-wrap' }, [
    h('table', { class: 'auto-agent-table' }, [
      h('thead', h('tr', columns.map(column => h('th', column)))),
      h('tbody', rows.slice(0, 12).map((row: Dict) => h('tr', columns.map(column => h('td', formatCell(row[column])))))),
    ]),
  ])
}

function renderRunList(runs: Dict[], selected: Dict | null, selectRun: (id: string) => void, cancelRun: () => void) {
  return h('section', { class: 'auto-agent-section' }, [
    sectionTitle({
      title: 'AI任务批次',
      subtitle: `${runs.length} 个批次`,
      icon: Tickets,
      tone: 'blue',
      aside: selected && ['queued', 'running'].includes(String(selected.status || ''))
        ? h('button', { class: 'secondary-action danger-action', onClick: cancelRun }, '停止')
        : null,
    }),
    runs.length
      ? h('div', { class: 'auto-agent-runs' }, runs.map(run => h('button', {
          class: ['auto-agent-run', selected?.id === run.id ? 'selected' : '', `status-${run.status}`],
          onClick: () => selectRun(String(run.id)),
        }, [
          h('strong', run.goal || (run.run_type === 'traffic_auto' ? 'AI自动引流' : 'AI自动拓客')),
          h('span', [statusLabel(String(run.status)), ' · ', run.created_at || '']),
        ])))
      : emptyState({ title: '暂无批次', description: '执行后会显示在这里。', icon: Tickets, tone: 'gray' }),
  ])
}

function renderEvents(events: Dict[]) {
  return h('section', { class: 'auto-agent-section' }, [
    sectionTitle({ title: '运行日志', subtitle: `${events.length} 条日志`, icon: DataLine, tone: 'amber', compact: true }),
    events.length
      ? h('div', { class: 'auto-agent-events' }, events.map(event => h('div', { class: ['auto-agent-event', `level-${event.level}`] }, [
          h('small', `${event.created_at || ''} · ${event.phase || ''}`),
          h('span', event.message || ''),
        ])))
      : emptyState({ title: '暂无日志', description: '批次开始后会持续刷新。', icon: DataLine, tone: 'gray' }),
  ])
}

function renderResult(run: Dict | null, isTraffic: boolean, router: ReturnType<typeof useRouter>) {
  const result = run?.result || {}
  const tiles = isTraffic
    ? [
        metricTile({ label: '处理视频', value: result.traffic_run?.browsed_count || 0, icon: VideoPlay, tone: 'blue' }),
        metricTile({ label: '成功动作', value: result.traffic_run?.action_success_count || 0, icon: Promotion, tone: 'green' }),
        metricTile({ label: '失败/跳过', value: Number(result.traffic_run?.failed_count || 0) + Number(result.traffic_run?.skipped_count || 0), icon: Tickets, tone: 'red' }),
      ]
    : [
        metricTile({ label: '竞品候选', value: result.competitor_candidates || 0, icon: MagicStick, tone: 'blue' }),
        metricTile({ label: '客户候选', value: result.customer_candidates || 0, icon: Message, tone: 'green' }),
        metricTile({ label: '私信成功', value: result.dm_success || 0, icon: Message, tone: 'teal' }),
        metricTile({ label: '失败/跳过', value: Number(result.dm_failed || 0) + Number(result.dm_skipped || 0), icon: Tickets, tone: 'red' }),
      ]
  return h('section', { class: 'auto-agent-section' }, [
    sectionTitle({
      title: '运行结果',
      subtitle: run ? statusLabel(String(run.status || '')) : '未选择批次',
      icon: VideoPlay,
      tone: 'green',
      compact: true,
    }),
    run ? h('div', { class: 'auto-agent-metrics' }, tiles) : emptyState({ title: '暂无结果', description: '选择批次后查看汇总。', icon: VideoPlay, tone: 'gray' }),
    run && isTraffic ? h('div', { class: 'action-row auto-agent-links' }, [
      h('button', { class: 'secondary-action', onClick: () => router.push('/traffic-monitor') }, '执行监控'),
      h('button', { class: 'secondary-action', onClick: () => router.push('/traffic-records') }, '操作记录'),
    ]) : null,
    run ? h('details', { class: 'auto-agent-advanced' }, [
      h('summary', '结果明细'),
      h('pre', JSON.stringify(result, null, 2)),
    ]) : null,
  ])
}

function actionLabel(action: string) {
  return {
    answer_stats: '统计查询',
    query_database: '数据库查询',
    lead_auto: '自动拓客',
    traffic_auto: '自动引流',
    unknown: '未识别',
  }[action] || action
}

function leadOperationLabel(operation: string) {
  return {
    full: '找竞品、找客户并私信',
    competitors: '只找竞品',
    customers: '找客户但不私信',
    message: '私信未触达客户',
  }[operation] || operation
}

function sourceModeLabel(mode: string) {
  return {
    search_keyword: '搜索关键词',
    random_feed: '随机推荐流',
    competitor_videos: '竞品视频',
    collected_keyword: '已采集关键词',
  }[mode] || mode
}

function actionName(action: string) {
  return {
    like: '点赞',
    collect: '收藏',
    follow: '关注',
    comment_text: '文字评论',
    comment_image: '图片评论',
  }[action] || action
}

function statusLabel(status: string) {
  return {
    queued: '排队中',
    running: '运行中',
    succeeded: '已完成',
    failed: '失败',
    cancelled: '已停止',
  }[status] || status
}

function formatCell(value: unknown) {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
