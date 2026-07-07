import { computed, defineComponent, h, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { DataLine, MagicStick, Message, Promotion, Refresh, Tickets, VideoPlay } from '@element-plus/icons-vue'

import { api } from '../shared/api'
import type { Dict } from '../shared/types'
import { TagInput } from '../components/ui/TagInput'
import { SplitPane } from '../components/ui/SplitPane'
import { emptyState, metricTile, pageAction, sectionTitle } from '../components/ui/Workbench'

const sourceOptions = [
  ['search_keyword', '手动搜索关键词'],
  ['random_feed', '随机推荐流'],
  ['competitor_videos', '拓客竞品视频'],
  ['collected_keyword', '已采集关键词'],
]

function defaultDraft() {
  return {
    goal: '',
    keywords: [] as string[],
    content_count: 20,
    comment_count: 20,
    dm_count: 10,
    interval_min_seconds: 30,
    interval_max_seconds: 60,
    source_mode: 'search_keyword',
    source_value: '',
    action_like: true,
    action_collect: false,
    action_follow: false,
    action_comment_text: false,
    action_comment_image: false,
    round_video_limit: 5,
  }
}

export default defineComponent({
  name: 'AutoAgentPage',
  setup() {
    const route = useRoute()
    const router = useRouter()
    const draft = ref<Dict>(defaultDraft())
    const settings = ref<Dict>({})
    const runs = ref<Dict[]>([])
    const selectedRun = ref<Dict | null>(null)
    const events = ref<Dict[]>([])
    const loading = ref(false)
    const submitting = ref(false)
    let timer: ReturnType<typeof window.setInterval> | null = null

    const runType = computed(() => route.name === 'traffic-auto' ? 'traffic_auto' : 'lead_auto')
    const isTraffic = computed(() => runType.value === 'traffic_auto')
    const title = computed(() => isTraffic.value ? 'AI自动引流' : 'AI自动拓客')
    const actionText = computed(() => isTraffic.value ? '执行自动引流' : '执行自动拓客')
    const productKeywords = computed(() => Array.isArray(settings.value.product_keywords) ? settings.value.product_keywords : [])

    onMounted(() => {
      loadAll()
      // 运行中的自动化批次用轮询刷新，避免首版引入 WebSocket。
      timer = window.setInterval(loadRunsAndEvents, 3000)
    })
    onBeforeUnmount(() => {
      if (timer) window.clearInterval(timer)
    })
    watch(runType, () => {
      draft.value = defaultDraft()
      selectedRun.value = null
      events.value = []
      loadAll()
    })

    async function loadAll() {
      loading.value = true
      try {
        await Promise.all([loadSettings(), loadRunsAndEvents()])
        applyKeywordDefaults()
      } finally {
        loading.value = false
      }
    }

    async function loadSettings() {
      const { data } = await api.get('/settings')
      settings.value = data
    }

    async function loadRunsAndEvents() {
      const { data } = await api.get('/agent/runs', { params: { run_type: runType.value } })
      runs.value = data
      const selectedId = selectedRun.value?.id
      const next = selectedId ? data.find((item: Dict) => item.id === selectedId) : data[0]
      if (next) await selectRun(next.id)
      else {
        selectedRun.value = null
        events.value = []
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

    function applyKeywordDefaults() {
      if (draft.value.keywords?.length) return
      draft.value.keywords = [...productKeywords.value]
      if (!draft.value.source_value && productKeywords.value.length) draft.value.source_value = productKeywords.value[0]
    }

    async function submitRun() {
      submitting.value = true
      try {
        const payload = buildPayload()
        const { data } = await api.post('/agent/runs', payload)
        selectedRun.value = data
        ElMessage.success('AI 自动化批次已创建')
        await loadRunsAndEvents()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || 'AI 自动化批次创建失败')
      } finally {
        submitting.value = false
      }
    }

    function buildPayload() {
      const base = {
        run_type: runType.value,
        platform: 'dy',
        goal: String(draft.value.goal || ''),
        keywords: draft.value.keywords || [],
      }
      if (isTraffic.value) {
        return {
          ...base,
          source_mode: draft.value.source_mode,
          source_value: draft.value.source_value,
          action_like: Boolean(draft.value.action_like),
          action_collect: Boolean(draft.value.action_collect),
          action_follow: Boolean(draft.value.action_follow),
          action_comment_text: Boolean(draft.value.action_comment_text),
          action_comment_image: Boolean(draft.value.action_comment_image),
          round_video_limit: Number(draft.value.round_video_limit || 5),
        }
      }
      return {
        ...base,
        content_count: Number(draft.value.content_count || 20),
        comment_count: Number(draft.value.comment_count || 20),
        dm_count: Number(draft.value.dm_count || 10),
        interval_min_seconds: Number(draft.value.interval_min_seconds || 30),
        interval_max_seconds: Number(draft.value.interval_max_seconds || 60),
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
          description: isTraffic.value ? '输入引流目标后创建计划并启动抖音引流批次。' : '输入目标后自动完成找竞品、筛客户和私信。',
          icon: isTraffic.value ? Promotion : MagicStick,
          tone: isTraffic.value ? 'blue' : 'teal',
        }),
        sectionTitle({ title: '我要做什么', subtitle: '只填目标和本次执行参数', icon: MagicStick, tone: 'teal' }),
        renderGoalField(draft.value),
        ...(isTraffic.value ? renderTrafficFields(draft.value) : renderLeadFields(draft.value)),
        h('div', { class: 'auto-agent-submit' }, [
          h('button', { class: 'primary-action', disabled: submitting.value, onClick: submitRun }, [h(VideoPlay, { class: 'inline-icon' }), submitting.value ? '创建中' : actionText.value]),
          h('button', { class: 'secondary-action', disabled: loading.value, onClick: loadAll }, [h(Refresh, { class: 'inline-icon' }), '刷新']),
        ]),
      ]),
      side: () => h('aside', { class: 'pane side-pane auto-agent-side' }, [
        renderRunList(runs.value, selectedRun.value, selectRun, cancelSelected),
        renderEvents(events.value),
        renderResult(selectedRun.value, isTraffic.value, router),
      ]),
    })
  },
})

function renderGoalField(draft: Dict) {
  return h('div', { class: 'auto-agent-block' }, [
    h('label', { class: 'auto-agent-field auto-agent-field-wide' }, [
      h('span', '目标描述'),
      h('textarea', {
        value: draft.goal,
        placeholder: '例如：为我寻找做跨境电商客服系统的潜在客户并私信',
        onInput: (event: Event) => { draft.goal = (event.target as HTMLTextAreaElement).value },
      }),
    ]),
    h('label', { class: 'auto-agent-field auto-agent-field-wide' }, [
      h('span', '关键词'),
      h(TagInput, {
        modelValue: draft.keywords || [],
        placeholder: '留空时由 AI 从设置页产品关键词中选择',
        'onUpdate:modelValue': (value: string[]) => { draft.keywords = value },
      }),
    ]),
  ])
}

function renderLeadFields(draft: Dict) {
  return [
    sectionTitle({ title: '私信参数', subtitle: `本次最多发送 ${draft.dm_count || 0} 个客户，间隔 ${draft.interval_min_seconds || 0}-${draft.interval_max_seconds || 0} 秒`, icon: Message, tone: 'green', compact: true }),
    h('div', { class: 'auto-agent-grid' }, [
      numberField(draft, 'dm_count', '私信数量', 1, 200),
      numberField(draft, 'interval_min_seconds', '最小间隔秒', 0, 3600),
      numberField(draft, 'interval_max_seconds', '最大间隔秒', 0, 3600),
    ]),
    h('details', { class: 'auto-agent-advanced' }, [
      h('summary', '高级参数'),
      h('div', { class: 'auto-agent-grid' }, [
        numberField(draft, 'content_count', '找竞品视频数', 1, 500),
        numberField(draft, 'comment_count', '找客户评论数', 0, 1000),
      ]),
    ]),
  ]
}

function renderTrafficFields(draft: Dict) {
  return [
    sectionTitle({ title: '引流参数', subtitle: '创建计划后立即启动批次', icon: Promotion, tone: 'blue', compact: true }),
    h('div', { class: 'auto-agent-grid' }, [
      h('label', { class: 'auto-agent-field' }, [
        h('span', '来源模式'),
        h('select', {
          value: draft.source_mode,
          onChange: (event: Event) => { draft.source_mode = (event.target as HTMLSelectElement).value },
        }, sourceOptions.map(([value, label]) => h('option', { value }, label))),
      ]),
      h('label', { class: 'auto-agent-field' }, [
        h('span', '来源值'),
        h('input', {
          value: draft.source_value || '',
          placeholder: '例如 AI客服',
          onInput: (event: Event) => { draft.source_value = (event.target as HTMLInputElement).value },
        }),
      ]),
      numberField(draft, 'round_video_limit', '每轮视频数', 1, 200),
    ]),
    h('div', { class: 'auto-agent-checks' }, [
      checkField(draft, 'action_like', '点赞'),
      checkField(draft, 'action_collect', '收藏'),
      checkField(draft, 'action_follow', '关注'),
      checkField(draft, 'action_comment_text', '文字评论'),
      checkField(draft, 'action_comment_image', '图片评论'),
    ]),
  ]
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
    sectionTitle({ title: '正在做什么', subtitle: `${events.length} 条日志`, icon: DataLine, tone: 'amber', compact: true }),
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
      title: '做完结果',
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

function numberField(draft: Dict, key: string, label: string, min: number, max: number) {
  return h('label', { class: 'auto-agent-field' }, [
    h('span', label),
    h('input', {
      type: 'number',
      min,
      max,
      value: draft[key],
      onInput: (event: Event) => { draft[key] = Number((event.target as HTMLInputElement).value) },
    }),
  ])
}

function checkField(draft: Dict, key: string, label: string) {
  return h('label', { class: 'auto-agent-check' }, [
    h('input', {
      type: 'checkbox',
      checked: Boolean(draft[key]),
      onChange: (event: Event) => { draft[key] = (event.target as HTMLInputElement).checked },
    }),
    h('span', label),
  ])
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
