import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ElAlert,
  ElButton,
  ElCard,
  ElCheckbox,
  ElCheckboxGroup,
  ElCol,
  ElDialog,
  ElDivider,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElProgress,
  ElRow,
  ElSelect,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
  ElTimePicker,
} from 'element-plus'
import { ChatDotRound, Clock, Connection, Promotion, Rank } from '@element-plus/icons-vue'

import { api } from '../shared/api'
import type { Dict } from '../shared/types'


const weekdays = [
  [1, '周一'], [2, '周二'], [3, '周三'], [4, '周四'], [5, '周五'], [6, '周六'], [7, '周日'],
]

export const automationPlanTypes = [
  ['keyword_lead', '关键词自动获客'],
  ['message', '自动私信'],
  ['traffic', '自动引流'],
]

export const trafficSourceOptions = [
  ['random_feed', '随机推荐流'],
  ['competitor_videos', '拓客竞品视频'],
  ['collected_keyword', '已采集关键词'],
  ['search_keyword', '手动搜索关键词'],
]

const trafficActions = [
  ['action_like', '点赞视频'],
  ['action_collect', '收藏视频'],
  ['action_follow', '关注作者'],
  ['action_comment_text', '文字评论'],
  ['action_comment_image', '图片评论'],
]

const emptyDraft = (planType = 'keyword_lead'): Dict => ({
  id: '',
  name: planType === 'message' ? '自动私信' : planType === 'traffic' ? '自动引流' : '关键词自动获客',
  plan_type: planType,
  weekdays: [1, 2, 3, 4, 5],
  run_time: '09:00',
  enabled: false,
  config: planType === 'message'
    ? {
        platform: 'dy', keyword_scope: 'all', keywords: [], count: 20,
        script_mode: 'ai', fixed_script: '', interval_min_seconds: 30, interval_max_seconds: 60, account_id: '',
      }
    : planType === 'traffic'
      ? {
          platform: 'dy', source_mode: 'random_feed', source_value: '',
          action_like: false, action_collect: false, action_follow: false,
          action_comment_text: false, action_comment_image: false, round_video_limit: 5, account_id: '',
        }
    : {
        platform: 'dy', keywords: [], keyword_count: 1, discovery_content_count: 20,
        competitor_limit: 30, competitor_content_count: 10, comment_count: 50,
        collect_sub_comments: false, auto_delete_non_competitors: false,
        auto_analyze_leads: true, auto_delete_non_customers: false,
        acquisition_account_id: '',
      },
})


// 返回拖动后的新数组，原数组保持不变，便于接口失败时恢复。
export function movePlan(items: Dict[], sourceId: string, targetId: string): Dict[] {
  const sourceIndex = items.findIndex(item => String(item.id) === sourceId)
  const targetIndex = items.findIndex(item => String(item.id) === targetId)
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return items
  const ordered = [...items]
  const [moved] = ordered.splice(sourceIndex, 1)
  ordered.splice(targetIndex, 0, moved)
  return ordered
}

export function normalizeTrafficConfig(config: Dict): Dict {
  if (config.platform !== 'ks') return { ...config }
  return { ...config, source_mode: 'random_feed', source_value: '', action_comment_image: false }
}


export default defineComponent({
  name: 'AutomationPlanPage',
  props: { refreshSeq: { type: Number, default: 0 } },
  setup(props) {
    const route = useRoute()
    const router = useRouter()
    const plans = ref<Dict[]>([])
    const runs = ref<Dict[]>([])
    const summary = ref<Dict>({})
    const accounts = ref<Dict[]>([])
    const limits = ref<Dict>({})
    const limitDraft = reactive({ daily_limit: 100, hourly_limit: 40 })
    const typePickerOpen = ref(false)
    const editorOpen = ref(false)
    const runDetailOpen = ref(false)
    const saving = ref(false)
    const selectedRun = ref<Dict>({})
    const draggedPlanId = ref('')
    const dragOverPlanId = ref('')
    const reordering = ref(false)
    const draft = reactive<Dict>(emptyDraft())

    const editing = computed(() => Boolean(draft.id))
    const planProgress = (run: Dict) => {
      const total = Number(run.total_count || 0)
      if (!total) return 0
      return Math.min(100, Math.round((Number(run.success_count || 0) + Number(run.failed_count || 0) + Number(run.skipped_count || 0)) * 100 / total))
    }

    onMounted(async () => {
      await loadAll()
      openFromRoute()
    })
    watch(() => props.refreshSeq, loadAll)
    watch(() => route.query.create, openFromRoute)

    async function loadAll() {
      try {
        const [planResponse, runResponse, limitResponse, accountResponse] = await Promise.all([
          api.get('/automation/plans'),
          api.get('/automation/runs', { params: { page: 1, page_size: 50 } }),
          api.get('/automation/message-limits'),
          api.get('/accounts'),
        ])
        plans.value = planResponse.data.items || []
        summary.value = planResponse.data.summary || {}
        runs.value = runResponse.data.items || []
        limits.value = limitResponse.data || {}
        accounts.value = accountResponse.data || []
        limitDraft.daily_limit = Number(limits.value.daily_limit || 100)
        limitDraft.hourly_limit = Number(limits.value.hourly_limit || 40)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '自动化计划加载失败')
      }
    }

    function openFromRoute() {
      if (String(route.query.create || '') !== '1') return
      const type = String(route.query.type || '')
      if (['keyword_lead', 'message', 'traffic'].includes(type)) openEditor(type)
      else typePickerOpen.value = true
      router.replace({ path: '/automation-plans', query: {} })
    }

    function openEditor(type: string, plan?: Dict) {
      Object.keys(draft).forEach(key => delete draft[key])
      Object.assign(draft, plan ? JSON.parse(JSON.stringify(plan)) : emptyDraft(type))
      selectDefaultAccount()
      typePickerOpen.value = false
      editorOpen.value = true
    }

    function setTrafficPlatform(platform: string) {
      draft.config = normalizeTrafficConfig({ ...(draft.config || {}), platform })
      selectDefaultAccount()
    }

    function featureAccounts(feature: string, platform: string) {
      return accounts.value.filter(account => account.platform === platform && account.enabled && account.status === 'ready' && account.features?.includes(feature))
    }

    function selectDefaultAccount() {
      const config = draft.config || {}
      const feature = draft.plan_type === 'keyword_lead' ? 'acquisition' : draft.plan_type
      const key = draft.plan_type === 'keyword_lead' ? 'acquisition_account_id' : 'account_id'
      const candidates = featureAccounts(feature, String(config.platform || 'dy'))
      if (candidates.some(account => account.id === config[key])) return
      const preferred = candidates.find(account => account.default_features?.includes(feature)) || candidates[0]
      config[key] = String(preferred?.id || '')
    }

    function accountField(config: Dict, key: string, feature: string) {
      const candidates = featureAccounts(feature, String(config.platform || 'dy'))
      return h(ElFormItem, { label: '执行账号', required: true }, () => h(ElSelect, {
        modelValue: config[key] || '',
        placeholder: candidates.length ? '请选择账号' : '账号中心暂无可用账号',
        'onUpdate:modelValue': (value: string) => config[key] = value,
      }, () => candidates.map(account => h(ElOption, { label: account.name, value: account.id }))))
    }

    function setTrafficSource(sourceMode: string) {
      if (draft.config.platform === 'ks' && sourceMode !== 'random_feed') {
        ElMessage.info('快手当前仅支持随机推荐流')
        return
      }
      draft.config = { ...(draft.config || {}), source_mode: sourceMode, source_value: '' }
    }

    function keywordText(config: Dict) {
      return Array.isArray(config.keywords) ? config.keywords.join('\n') : ''
    }

    function setKeywords(config: Dict, value: string) {
      config.keywords = String(value || '').split(/[\n,，]/).map(item => item.trim()).filter(Boolean)
    }

    async function savePlan() {
      if (!String(draft.name || '').trim()) {
        ElMessage.warning('请填写计划名称')
        return
      }
      if (!Array.isArray(draft.weekdays) || !draft.weekdays.length) {
        ElMessage.warning('请至少选择一个运行星期')
        return
      }
      const accountKey = draft.plan_type === 'keyword_lead' ? 'acquisition_account_id' : 'account_id'
      if (!String(draft.config?.[accountKey] || '')) {
        ElMessage.warning('请选择执行账号；登录和用途分配请到账号中心完成')
        return
      }
      if (draft.plan_type === 'traffic') {
        draft.config = normalizeTrafficConfig(draft.config || {})
        if (['collected_keyword', 'search_keyword'].includes(draft.config.source_mode) && !String(draft.config.source_value || '').trim()) {
          ElMessage.warning('请填写引流来源内容')
          return
        }
      }
      saving.value = true
      try {
        const payload = {
          name: String(draft.name || '').trim(),
          weekdays: draft.weekdays,
          run_time: draft.run_time,
          config: draft.config,
          enabled: Boolean(draft.enabled),
          ...(editing.value ? {} : { plan_type: draft.plan_type }),
        }
        if (editing.value) await api.patch(`/automation/plans/${draft.id}`, payload)
        else await api.post('/automation/plans', payload)
        ElMessage.success(editing.value ? '计划已保存' : '计划已创建')
        editorOpen.value = false
        await loadAll()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '计划保存失败')
      } finally {
        saving.value = false
      }
    }

    async function togglePlan(plan: Dict, enabled: boolean) {
      try {
        await api.patch(`/automation/plans/${plan.id}`, { enabled })
        ElMessage.success(enabled ? '计划已启用' : '计划已停用')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '计划状态修改失败')
      } finally {
        await loadAll()
      }
    }

    async function persistPlanMove(sourceId: string, targetId: string) {
      const previous = plans.value
      const ordered = movePlan(previous, sourceId, targetId)
      if (ordered === previous || reordering.value) return
      plans.value = ordered
      reordering.value = true
      try {
        const { data } = await api.put('/automation/plans/order', { plan_ids: ordered.map(plan => String(plan.id)) })
        plans.value = data.items || ordered
        summary.value = data.summary || summary.value
        ElMessage.success('计划执行顺序已保存')
      } catch (error: any) {
        plans.value = previous
        ElMessage.error(error?.response?.data?.detail || '计划顺序保存失败')
      } finally {
        reordering.value = false
        draggedPlanId.value = ''
        dragOverPlanId.value = ''
      }
    }

    function startPlanDrag(plan: Dict, event: DragEvent) {
      if (reordering.value) return event.preventDefault()
      draggedPlanId.value = String(plan.id)
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = 'move'
        event.dataTransfer.setData('text/plain', draggedPlanId.value)
      }
    }

    function overPlan(plan: Dict, event: DragEvent) {
      if (!draggedPlanId.value || draggedPlanId.value === String(plan.id)) return
      event.preventDefault()
      dragOverPlanId.value = String(plan.id)
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
    }

    function dropPlan(plan: Dict, event: DragEvent) {
      event.preventDefault()
      const sourceId = draggedPlanId.value || event.dataTransfer?.getData('text/plain') || ''
      void persistPlanMove(sourceId, String(plan.id))
    }

    function movePlanByKeyboard(plan: Dict, index: number, event: KeyboardEvent) {
      if (!event.altKey || !['ArrowUp', 'ArrowDown'].includes(event.key)) return
      const targetIndex = index + (event.key === 'ArrowUp' ? -1 : 1)
      const target = plans.value[targetIndex]
      if (!target) return
      event.preventDefault()
      void persistPlanMove(String(plan.id), String(target.id))
    }

    async function runNow(plan: Dict) {
      try {
        const { data } = await api.post(`/automation/plans/${plan.id}/run`)
        ElMessage.success(data.status === 'skipped' ? '已有同计划运行，本次已跳过' : '计划已加入运行队列')
        await loadAll()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '计划启动失败')
      }
    }

    async function archivePlan(plan: Dict) {
      try {
        await ElMessageBox.confirm(`归档计划“${plan.name}”？`, '归档计划', { type: 'warning' })
        await api.post(`/automation/plans/${plan.id}/archive`)
        ElMessage.success('计划已归档')
        await loadAll()
      } catch (error: any) {
        if (error === 'cancel' || error === 'close') return
        ElMessage.error(error?.response?.data?.detail || '归档失败')
      }
    }

    async function showRun(run: Dict) {
      try {
        const { data } = await api.get(`/automation/runs/${run.id}`)
        selectedRun.value = data
        runDetailOpen.value = true
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '运行详情加载失败')
      }
    }

    async function cancelRun(run: Dict) {
      try {
        await api.post(`/automation/runs/${run.id}/cancel`)
        ElMessage.success('已请求停止计划')
        await loadAll()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '停止计划失败')
      }
    }

    async function saveLimits() {
      try {
        const { data } = await api.put('/automation/message-limits', limitDraft)
        limits.value = data
        ElMessage.success('私信额度已保存')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '私信额度保存失败')
      }
    }

    function metric(label: string, value: unknown, tone = '') {
      return h('div', { class: ['automation-metric', tone] }, [h('span', label), h('strong', String(value ?? '—'))])
    }

    function renderTaskCards() {
      return h(ElRow, { gutter: 16, class: 'automation-task-cards' }, () => [
        h(ElCol, { xs: 24, md: 8 }, () => h(ElCard, { shadow: 'hover', class: 'automation-task-card' }, () => [
          h('div', { class: 'automation-card-icon lead' }, [h(Promotion)]),
          h('div', { class: 'automation-card-content' }, [
            h('h3', '关键词自动获客'),
            h('p', '按优先级选择关键词，依次采竞品、AI筛选、采评论并可选分析和清理无关对象。'),
            h('div', { class: 'automation-flow' }, '选择关键词 → 采竞品 → AI筛选 → 采评论 → 客户分析'),
          ]),
          h(ElButton, { type: 'primary', onClick: () => openEditor('keyword_lead') }, () => '新建获客计划'),
        ])),
        h(ElCol, { xs: 24, md: 8 }, () => h(ElCard, { shadow: 'hover', class: 'automation-task-card' }, () => [
          h('div', { class: 'automation-card-icon message' }, [h(ChatDotRound)]),
          h('div', { class: 'automation-card-content' }, [
            h('h3', '自动私信'),
            h('p', '独立消化新旧积压客户，高意向优先，同意向下等待最久的客户优先。'),
            h('div', { class: 'automation-flow' }, '筛选积压客户 → 占用额度 → 按间隔逐个私信'),
          ]),
          h(ElButton, { type: 'primary', onClick: () => openEditor('message') }, () => '新建私信计划'),
        ])),
        h(ElCol, { xs: 24, md: 8 }, () => h(ElCard, { shadow: 'hover', class: 'automation-task-card' }, () => [
          h('div', { class: 'automation-card-icon traffic' }, [h(Connection)]),
          h('div', { class: 'automation-card-content' }, [
            h('h3', '自动引流'),
            h('p', '定时启动抖音或快手引流批次，与获客、私信计划共用浏览器队列。'),
            h('div', { class: 'automation-flow' }, '选择来源 → 浏览视频 → 按组合执行互动 → 记录结果'),
          ]),
          h(ElButton, { type: 'primary', onClick: () => openEditor('traffic') }, () => '新建引流计划'),
        ])),
      ])
    }

    function renderLimits() {
      const configured = Boolean(limits.value.configured)
      return h(ElCard, { class: 'automation-section', shadow: 'never' }, () => [
        h('div', { class: 'automation-section-head' }, [
          h('div', [h('h2', '私信频率额度'), h('p', '单次、批量和定时私信共用；失败或结果不明确也会占用额度。')]),
          h(ElButton, { type: 'primary', onClick: saveLimits }, () => '保存额度'),
        ]),
        h('div', { class: 'automation-limit-grid' }, [
          h(ElFormItem, { label: '每日不同用户上限' }, () => h(ElInputNumber, {
            modelValue: limitDraft.daily_limit, min: 1, max: 100,
            'onUpdate:modelValue': (value: number | undefined) => { if (value !== undefined) limitDraft.daily_limit = value },
          })),
          h(ElFormItem, { label: '每小时不同用户上限' }, () => h(ElInputNumber, {
            modelValue: limitDraft.hourly_limit, min: 1, max: 40,
            'onUpdate:modelValue': (value: number | undefined) => { if (value !== undefined) limitDraft.hourly_limit = value },
          })),
          metric('本小时已占用 / 剩余', configured ? `${limits.value.used_hour} / ${limits.value.remaining_hour}` : '未配置', 'blue'),
          metric('今日已占用 / 剩余', configured ? `${limits.value.used_today} / ${limits.value.remaining_today}` : '未配置', 'green'),
        ]),
        h(ElAlert, {
          title: limits.value.notice || '仅统计本软件产生的私信，无法感知抖音 App 内手动发送数量。',
          type: limits.value.fill_only ? 'error' : 'warning', showIcon: true, closable: false,
          description: limits.value.fill_only ? '当前已开启“只填内容不发送”，自动私信计划不能启用或运行。' : '',
        }),
      ])
    }

    function renderPlans() {
      return h(ElCard, { class: 'automation-section', shadow: 'never' }, () => [
        h('div', { class: 'automation-section-head' }, [h('div', [h('h2', '计划列表'), h('p', '拖动计划调整顺序；相同时间从上到下逐个执行，前一个结束后再执行下一个。')])]),
        h(ElTable, { data: plans.value, stripe: true, emptyText: '还没有自动化计划' }, () => [
          h(ElTableColumn, { label: '计划', minWidth: 210 }, { default: ({ row, $index }: Dict) => h('div', {
            class: ['automation-plan-drag-cell', { 'is-dragging': draggedPlanId.value === String(row.id), 'is-over': dragOverPlanId.value === String(row.id) }],
            draggable: !reordering.value,
            tabindex: 0,
            'aria-label': `第${$index + 1}项，${row.name}。拖动排序，或按 Alt 加上下方向键调整`,
            title: '按住拖动调整执行顺序',
            onDragstart: (event: DragEvent) => startPlanDrag(row, event),
            onDragover: (event: DragEvent) => overPlan(row, event),
            onDragleave: () => { if (dragOverPlanId.value === String(row.id)) dragOverPlanId.value = '' },
            onDrop: (event: DragEvent) => dropPlan(row, event),
            onDragend: () => { draggedPlanId.value = ''; dragOverPlanId.value = '' },
            onKeydown: (event: KeyboardEvent) => movePlanByKeyboard(row, Number($index), event),
          }, [
            h('span', { class: 'automation-drag-handle', 'aria-hidden': 'true' }, [h(Rank), h('b', String($index + 1))]),
            h('div', [h('strong', row.name), h('small', { class: 'table-subtext' }, planTypeLabel(row.plan_type))]),
          ]) }),
          h(ElTableColumn, { label: '执行时间', minWidth: 190 }, { default: ({ row }: Dict) => h('div', [h('span', `${weekdayText(row.weekdays)} ${row.run_time}`), h('small', { class: 'table-subtext' }, row.next_run_at ? `下次 ${row.next_run_at}` : '已停用')]) }),
          h(ElTableColumn, { label: '范围', minWidth: 190 }, { default: ({ row }: Dict) => h('span', scopeText(row)) }),
          h(ElTableColumn, { label: '最近结果', width: 120 }, { default: ({ row }: Dict) => row.recent_run ? statusTag(row.recent_run.status) : h('span', '—') }),
          h(ElTableColumn, { label: '启用', width: 90 }, { default: ({ row }: Dict) => h(ElSwitch, { modelValue: row.enabled, 'onUpdate:modelValue': (value: string | number | boolean) => togglePlan(row, Boolean(value)) }) }),
          h(ElTableColumn, { label: '操作', width: 240, fixed: 'right' }, { default: ({ row }: Dict) => h('div', { class: 'table-actions' }, [
            h(ElButton, { text: true, type: 'primary', onClick: () => runNow(row) }, () => '立即运行'),
            h(ElButton, { text: true, onClick: () => openEditor(row.plan_type, row) }, () => '编辑'),
            h(ElButton, { text: true, type: 'danger', onClick: () => archivePlan(row) }, () => '归档'),
          ]) }),
        ]),
      ])
    }

    function renderRuns() {
      return h(ElCard, { class: 'automation-section', shadow: 'never' }, () => [
        h('div', { class: 'automation-section-head' }, [h('div', [h('h2', '执行记录'), h('p', '查看当前阶段、关键词进度、关联任务、私信数量和错误原因。')])]),
        h(ElTable, { data: runs.value, stripe: true, emptyText: '暂无执行记录' }, () => [
          h(ElTableColumn, { prop: 'created_at', label: '开始时间', width: 170 }),
          h(ElTableColumn, { label: '计划', minWidth: 160 }, { default: ({ row }: Dict) => h('div', [h('strong', row.plan_name), h('small', { class: 'table-subtext' }, row.trigger_type === 'manual' ? '立即运行' : '定时触发')]) }),
          h(ElTableColumn, { label: '状态', width: 110 }, { default: ({ row }: Dict) => statusTag(row.status) }),
          h(ElTableColumn, { label: '当前阶段', minWidth: 140 }, { default: ({ row }: Dict) => h('span', stageLabel(row.current_stage)) }),
          h(ElTableColumn, { label: '进度', width: 170 }, { default: ({ row }: Dict) => row.plan_type === 'message'
            ? h('span', `${row.message_success_count || 0} 成功 / ${row.message_attempted_count || 0} 尝试`)
            : row.plan_type === 'traffic'
              ? h('span', trafficProgressText(row))
              : h(ElProgress, { percentage: planProgress(row), strokeWidth: 8 }) }),
          h(ElTableColumn, { label: '结果', minWidth: 210 }, { default: ({ row }: Dict) => h('span', runResultText(row)) }),
          h(ElTableColumn, { label: '操作', width: 150, fixed: 'right' }, { default: ({ row }: Dict) => h('div', { class: 'table-actions' }, [
            h(ElButton, { text: true, type: 'primary', onClick: () => showRun(row) }, () => '详情'),
            ['queued', 'running'].includes(row.status) ? h(ElButton, { text: true, type: 'danger', onClick: () => cancelRun(row) }, () => '停止') : null,
          ]) }),
        ]),
      ])
    }

    function renderTypePicker() {
      const descriptions: Dict = {
        keyword_lead: '定时采集竞品、评论并筛选目标客户',
        message: '定时消化新旧积压客户并统一控制私信额度',
        traffic: '定时启动抖音或快手浏览与互动批次',
      }
      const icons: Dict = { keyword_lead: Promotion, message: ChatDotRound, traffic: Connection }
      return h(ElDialog, {
        modelValue: typePickerOpen.value,
        title: '选择自动化任务类型',
        width: '680px',
        'onUpdate:modelValue': (value: boolean) => typePickerOpen.value = value,
      }, { default: () => h('div', { class: 'automation-type-picker' }, automationPlanTypes.map(([type, label]) => h(ElCard, {
        shadow: 'hover',
        class: `automation-type-option type-${type}`,
        tabindex: 0,
        onClick: () => openEditor(type),
        onKeydown: (event: KeyboardEvent) => { if (['Enter', ' '].includes(event.key)) openEditor(type) },
      }, () => [
        h('div', { class: ['automation-card-icon', type === 'keyword_lead' ? 'lead' : type] }, [h(icons[type])]),
        h('strong', label),
        h('p', descriptions[type]),
        h(ElButton, { type: 'primary', plain: true }, () => `新建${label}计划`),
      ]))) })
    }

    function renderEditor() {
      return h(ElDialog, {
        modelValue: editorOpen.value, title: `${editing.value ? '编辑' : '新建'}${planTypeLabel(draft.plan_type)}`,
        width: '720px', destroyOnClose: true, 'onUpdate:modelValue': (value: boolean) => editorOpen.value = value,
      }, {
        default: () => h(ElForm, { labelPosition: 'top', class: 'automation-editor' }, () => [
          h(ElFormItem, { label: '计划名称', required: true }, () => h(ElInput, { modelValue: draft.name, maxlength: 80, 'onUpdate:modelValue': (value: string) => draft.name = value })),
          h(ElRow, { gutter: 16 }, () => [
            h(ElCol, { span: 16 }, () => h(ElFormItem, { label: '运行星期', required: true }, () => h(ElCheckboxGroup, { modelValue: draft.weekdays, 'onUpdate:modelValue': (value: Array<string | number>) => draft.weekdays = value.map(Number) }, () => weekdays.map(([value, label]) => h(ElCheckbox, { value }, () => label))))),
            h(ElCol, { span: 8 }, () => h(ElFormItem, { label: '开始时间', required: true }, () => h(ElTimePicker, { modelValue: timeValue(draft.run_time), format: 'HH:mm', valueFormat: 'HH:mm', 'onUpdate:modelValue': (value: string) => draft.run_time = value }))),
          ]),
          h(ElFormItem, { label: '创建后启用' }, () => h(ElSwitch, { modelValue: draft.enabled, 'onUpdate:modelValue': (value: string | number | boolean) => draft.enabled = Boolean(value) })),
          h(ElDivider, { contentPosition: 'left' }, () => '固定执行步骤'),
          h('div', { class: 'automation-step-preview' }, draft.plan_type === 'keyword_lead'
            ? '选择关键词 → 采集竞品候选 → 竞品AI筛选（固定） → 采集评论 → 可选客户意向分析'
            : draft.plan_type === 'message'
              ? '选择新旧积压客户 → 高意向优先排序 → 占用统一额度 → 逐个私信'
              : '创建引流批次 → 等待共用浏览器 → 浏览视频 → 执行互动 → 记录结果'),
          ...(draft.plan_type === 'keyword_lead' ? renderKeywordFields() : draft.plan_type === 'message' ? renderMessageFields() : renderTrafficFields()),
        ]),
        footer: () => h('div', [h(ElButton, { onClick: () => editorOpen.value = false }, () => '取消'), h(ElButton, { type: 'primary', loading: saving.value, onClick: savePlan }, () => '保存计划')]),
      })
    }

    function renderKeywordFields() {
      const config = draft.config as Dict
      return [
        accountField(config, 'acquisition_account_id', 'acquisition'),
        h(ElFormItem, { label: '关键词（每行一个）', required: true }, () => h(ElInput, { type: 'textarea', rows: 4, modelValue: keywordText(config), placeholder: '例如：企业获客\n短视频营销', 'onUpdate:modelValue': (value: string) => setKeywords(config, value) })),
        h(ElRow, { gutter: 16 }, () => [
          numberField('每次选择关键词', config, 'keyword_count', 1, 10),
          numberField('竞品候选视频数', config, 'discovery_content_count', 1, 100),
          numberField('竞品账号上限', config, 'competitor_limit', 1, 100),
        ]),
        h(ElRow, { gutter: 16 }, () => [
          numberField('每个竞品内容数', config, 'competitor_content_count', 1, 100),
          numberField('每条内容评论数', config, 'comment_count', 1, 1000),
          h(ElCol, { span: 8 }, () => h(ElFormItem, { label: '采集子评论' }, () => h(ElSwitch, { modelValue: config.collect_sub_comments, 'onUpdate:modelValue': (value: string | number | boolean) => config.collect_sub_comments = Boolean(value) }))),
        ]),
        h(ElFormItem, { label: '竞品筛选后清理' }, () => h(ElSwitch, { modelValue: config.auto_delete_non_competitors, activeText: '自动删除非竞品', inactiveText: '保留分析记录', 'onUpdate:modelValue': (value: string | number | boolean) => config.auto_delete_non_competitors = Boolean(value) })),
        h(ElFormItem, { label: '客户意向分析' }, () => h(ElSwitch, {
          modelValue: config.auto_analyze_leads,
          activeText: '采集后自动分析',
          inactiveText: '仅保留待筛选客户',
          'onUpdate:modelValue': (value: string | number | boolean) => {
            config.auto_analyze_leads = Boolean(value)
            if (!config.auto_analyze_leads) config.auto_delete_non_customers = false
          }
        })),
        config.auto_analyze_leads ? h(ElFormItem, { label: '客户分析后清理' }, () => h(ElSwitch, { modelValue: config.auto_delete_non_customers, activeText: '自动删除非客户', inactiveText: '保留分析记录', 'onUpdate:modelValue': (value: string | number | boolean) => config.auto_delete_non_customers = Boolean(value) })) : null,
        h(ElAlert, { title: '竞品 AI 筛选是采评论的必要步骤。需要自动私信时，可创建同一时间的“自动私信”计划并拖到本计划之后，系统会按顺序执行。', type: 'info', showIcon: true, closable: false }),
      ]
    }

    function renderMessageFields() {
      const config = draft.config as Dict
      return [
        accountField(config, 'account_id', 'message'),
        h(ElRow, { gutter: 16 }, () => [
          h(ElCol, { span: 12 }, () => h(ElFormItem, { label: '关键词范围' }, () => h(ElSelect, { modelValue: config.keyword_scope, 'onUpdate:modelValue': (value: string) => config.keyword_scope = value }, () => [h(ElOption, { label: '全部关键词', value: 'all' }), h(ElOption, { label: '指定关键词', value: 'selected' })]))),
          numberField('本次目标数量', config, 'count', 1, 100, 12),
        ]),
        config.keyword_scope === 'selected' ? h(ElFormItem, { label: '指定关键词（每行一个）', required: true }, () => h(ElInput, { type: 'textarea', rows: 3, modelValue: keywordText(config), 'onUpdate:modelValue': (value: string) => setKeywords(config, value) })) : null,
        h(ElFormItem, { label: '私信话术' }, () => h(ElSelect, { modelValue: config.script_mode, 'onUpdate:modelValue': (value: string) => config.script_mode = value }, () => [h(ElOption, { label: '使用客户已有 AI 话术', value: 'ai' }), h(ElOption, { label: '使用本计划固定话术', value: 'fixed' })])),
        config.script_mode === 'fixed' ? h(ElFormItem, { label: '固定话术', required: true }, () => h(ElInput, { type: 'textarea', rows: 4, maxlength: 2000, showWordLimit: true, modelValue: config.fixed_script, 'onUpdate:modelValue': (value: string) => config.fixed_script = value })) : null,
        h(ElRow, { gutter: 16 }, () => [numberField('最小随机间隔（秒）', config, 'interval_min_seconds', 0, 3600, 12), numberField('最大随机间隔（秒）', config, 'interval_max_seconds', 0, 3600, 12)]),
        h(ElAlert, { title: '候选客户必须是抖音目标客户、未私信、未隐藏、主页可用且今天未尝试。AI话术模式还要求客户已有AI话术。', type: 'info', showIcon: true, closable: false }),
      ]
    }

    function renderTrafficFields() {
      const config = draft.config as Dict
      const sourceMode = String(config.source_mode || 'random_feed')
      const sourceLabel = sourceMode === 'competitor_videos'
        ? '竞品视频链接或 ID（每行一条）'
        : sourceMode === 'collected_keyword' ? '已采集关键词' : '搜索关键词'
      const sourceRequired = ['collected_keyword', 'search_keyword'].includes(sourceMode)
      return [
        accountField(config, 'account_id', 'traffic'),
        h(ElRow, { gutter: 16 }, () => [
          h(ElCol, { span: 8 }, () => h(ElFormItem, { label: '平台', required: true }, () => h(ElSelect, {
            modelValue: config.platform,
            'onUpdate:modelValue': setTrafficPlatform,
          }, () => [h(ElOption, { label: '抖音', value: 'dy' }), h(ElOption, { label: '快手', value: 'ks' })]))),
          h(ElCol, { span: 8 }, () => h(ElFormItem, { label: '来源模式', required: true }, () => h(ElSelect, {
            modelValue: sourceMode,
            'onUpdate:modelValue': setTrafficSource,
          }, () => trafficSourceOptions.map(([value, label]) => h(ElOption, { label, value, disabled: config.platform === 'ks' && value !== 'random_feed' }))))),
          numberField('每轮视频数量', config, 'round_video_limit', 1, 200),
        ]),
        sourceMode !== 'random_feed' ? h(ElFormItem, { label: sourceLabel, required: sourceRequired }, () => h(ElInput, {
          type: sourceMode === 'competitor_videos' ? 'textarea' : 'text',
          rows: sourceMode === 'competitor_videos' ? 4 : undefined,
          modelValue: config.source_value,
          placeholder: sourceMode === 'competitor_videos' ? '可填写多条视频链接或 ID；留空时使用全部已采集竞品视频' : '填写一个关键词',
          'onUpdate:modelValue': (value: string) => config.source_value = value,
        })) : null,
        h(ElFormItem, { label: '动作组合（不选表示纯浏览）' }, () => h('div', { class: 'automation-action-options' }, trafficActions.map(([key, label]) => h(ElCheckbox, {
          modelValue: Boolean(config[key]),
          disabled: config.platform === 'ks' && key === 'action_comment_image',
          'onUpdate:modelValue': (value: string | number | boolean) => config[key] = Boolean(value),
        }, () => label)))),
        h('div', { class: 'automation-traffic-summary' }, trafficActionLabel(config).length
          ? `将执行：${trafficActionLabel(config).join('、')}`
          : '纯浏览：只浏览并记录视频，不执行点赞、收藏、关注或评论。'),
        h(ElAlert, {
          title: config.platform === 'ks'
            ? '快手仅支持随机推荐流，且不支持图片评论。'
            : '抖音支持随机推荐流、竞品视频、已采集关键词和手动搜索关键词。',
          description: '停留时间、动作概率、每日动作上限、失败停机规则和评论素材会在执行时读取“引流设置”的最新值。',
          type: 'info', showIcon: true, closable: false,
        }),
        h(ElButton, { text: true, type: 'primary', onClick: () => { editorOpen.value = false; router.push('/traffic-settings') } }, () => '前往引流设置'),
      ]
    }

    function numberField(label: string, target: Dict, key: string, min: number, max: number, span = 8) {
      return h(ElCol, { span }, () => h(ElFormItem, { label }, () => h(ElInputNumber, { modelValue: target[key], min, max, controlsPosition: 'right', 'onUpdate:modelValue': (value: number | undefined) => { if (value !== undefined) target[key] = value } })))
    }

    function renderRunDetail() {
      const run = selectedRun.value
      const traffic = trafficRunData(run)
      const trafficRunId = String(run.traffic_run_id || traffic.id || '')
      return h(ElDialog, { modelValue: runDetailOpen.value, title: `执行详情 · ${run.plan_name || ''}`, width: '820px', 'onUpdate:modelValue': (value: boolean) => runDetailOpen.value = value }, { default: () => [
        h('div', { class: 'automation-run-summary' }, run.plan_type === 'traffic'
          ? [statusTag(run.status), h('span', `阶段：${stageLabel(run.current_stage)}`), h('span', `浏览 ${traffic.browsed_count || 0}`), h('span', `成功动作 ${traffic.action_success_count || 0}`), h('span', `失败/跳过 ${(traffic.failed_count || 0) + (traffic.skipped_count || 0)}`)]
          : [statusTag(run.status), h('span', `阶段：${stageLabel(run.current_stage)}`), h('span', `成功 ${run.success_count || 0}`), h('span', `失败 ${run.failed_count || 0}`), h('span', `跳过 ${run.skipped_count || 0}`)]),
        run.error ? h(ElAlert, { title: run.error, type: run.status === 'partial' ? 'warning' : 'error', showIcon: true, closable: false }) : null,
        run.plan_type === 'message' ? h('div', { class: 'automation-message-result' }, `私信批次 ${run.message_batch_id || '—'}：尝试 ${run.message_attempted_count || 0}，成功 ${run.message_success_count || 0}`) : null,
        run.plan_type === 'traffic' ? h('div', { class: 'automation-traffic-result' }, [
          h('div', { class: 'automation-traffic-result-grid' }, [
            metric('关联引流批次', trafficRunId ? shortId(trafficRunId) : '尚未创建'),
            metric('浏览视频', traffic.browsed_count || 0, 'blue'),
            metric('成功动作', traffic.action_success_count || 0, 'green'),
            metric('失败 / 跳过', `${traffic.failed_count || 0} / ${traffic.skipped_count || 0}`, 'red'),
          ]),
          trafficRunId ? h(ElButton, {
            type: 'primary',
            onClick: () => { runDetailOpen.value = false; router.push({ path: '/traffic-monitor', query: { run: trafficRunId } }) },
          }, () => '前往引流执行监控') : null,
        ]) : null,
        (run.items || []).length ? h(ElTable, { data: run.items, stripe: true }, () => [
          h(ElTableColumn, { prop: 'keyword', label: '关键词', width: 140 }),
          h(ElTableColumn, { label: '状态', width: 100 }, { default: ({ row }: Dict) => statusTag(row.status) }),
          h(ElTableColumn, { label: '阶段', width: 140 }, { default: ({ row }: Dict) => stageLabel(row.current_stage) }),
          h(ElTableColumn, { label: '关联任务', minWidth: 230 }, { default: ({ row }: Dict) => h('div', { class: 'automation-related-jobs' }, relatedJobIds(row.context).map(id => h(ElTag, { size: 'small' }, () => id))) }),
          h(ElTableColumn, { label: 'AI / 客户', width: 130 }, { default: ({ row }: Dict) => h('span', `AI ${row.ai_results?.filter((item: Dict) => item.status === 'succeeded').length || 0}/${row.ai_results?.length || 0} · 客户 ${row.lead_count || 0}`) }),
          h(ElTableColumn, { prop: 'error', label: '错误原因', minWidth: 180 }),
        ]) : null,
      ] })
    }

    return () => h('div', { class: 'automation-page' }, [
      h('section', { class: 'automation-hero' }, [
        h('div', [h('div', { class: 'automation-eyebrow' }, [h(Clock), h('span', '跨工作台自动化')]), h('h1', '自动化计划'), h('p', '按指定星期和时间运行获客、私信或引流流程；计划按列表顺序共用浏览器队列。软件未运行时错过的计划会直接跳过。')]),
        h('div', { class: 'automation-metrics' }, [metric('已启用计划', summary.value.enabled || 0, 'green'), metric('下一个计划', summary.value.next_run_at || '暂无', 'blue'), metric('正在运行', summary.value.running || 0, 'blue'), metric('今日失败', summary.value.failed_today || 0, 'red')]),
      ]),
      renderTaskCards(),
      renderLimits(),
      renderPlans(),
      renderRuns(),
      renderTypePicker(),
      renderEditor(),
      renderRunDetail(),
    ])
  },
})


function planTypeLabel(type: string) {
  return Object.fromEntries(automationPlanTypes)[type] || type || '未知计划'
}

function weekdayText(values: number[]) {
  const selected = Array.isArray(values) ? values : []
  if (selected.length === 7) return '每天'
  if (selected.join(',') === '1,2,3,4,5') return '工作日'
  return selected.map(value => weekdays.find(item => item[0] === value)?.[1] || value).join('、')
}

function scopeText(plan: Dict) {
  const config = plan.config || {}
  if (plan.plan_type === 'message') return config.keyword_scope === 'all' ? `全部关键词 · ${config.count || 0}人` : `${(config.keywords || []).join('、')} · ${config.count || 0}人`
  if (plan.plan_type === 'traffic') return `${trafficPlatformLabel(config.platform)} · ${trafficSourceLabel(config.source_mode)} · ${trafficActionLabel(config).join('、') || '纯浏览'} · ${config.round_video_limit || 0}条`
  return `${(config.keywords || []).length}个关键词 · 每次${config.keyword_count || 1}个`
}

function trafficPlatformLabel(platform: string) {
  return platform === 'ks' ? '快手' : '抖音'
}

function trafficSourceLabel(sourceMode: string) {
  return Object.fromEntries(trafficSourceOptions)[sourceMode] || sourceMode || '未知来源'
}

function trafficActionLabel(config: Dict) {
  return trafficActions.filter(([key]) => Boolean(config?.[key])).map(([, label]) => label)
}

function trafficRunData(run: Dict): Dict {
  return run.traffic_run || run.traffic_summary || {}
}

function trafficProgressText(run: Dict) {
  const traffic = trafficRunData(run)
  if (!run.traffic_run_id && !traffic.id) return ['queued', 'running'].includes(run.status) ? '等待创建批次' : '未创建批次'
  const total = Number(run.config_snapshot?.round_video_limit || run.total_count || 0)
  if (!traffic.id) return ['queued', 'running'].includes(run.status) ? `目标 ${total} 个视频` : `已处理 ${run.total_count || 0} 个视频`
  return total ? `${traffic.browsed_count || 0} / ${total} 个视频` : `${traffic.browsed_count || 0} 个视频`
}

function runResultText(run: Dict) {
  if (run.error) return run.error
  if (run.plan_type === 'message') return `${run.message_success_count || 0} 成功，${run.message_attempted_count || 0} 尝试`
  if (run.plan_type === 'traffic') {
    const traffic = trafficRunData(run)
    return traffic.id
      ? `浏览 ${traffic.browsed_count || 0} · 动作 ${traffic.action_success_count || 0} · 失败/跳过 ${(traffic.failed_count || 0) + (traffic.skipped_count || 0)}`
      : `动作 ${run.success_count || 0} · 失败/跳过 ${(run.failed_count || 0) + (run.skipped_count || 0)}`
  }
  return `${run.success_count || 0} 成功，${run.failed_count || 0} 失败`
}

function statusTag(status: string) {
  const labels: Dict = { queued: '排队中', running: '运行中', completed: '完成', partial: '部分完成', failed: '失败', skipped: '已跳过', cancelled: '已停止', pending: '待执行', succeeded: '成功' }
  const types: Dict = { completed: 'success', succeeded: 'success', partial: 'warning', failed: 'danger', cancelled: 'info', skipped: 'info', running: 'primary' }
  return h(ElTag, { type: types[status] || 'info', effect: status === 'running' ? 'dark' : 'light' }, () => labels[status] || status || '—')
}

function stageLabel(stage: string) {
  const labels: Dict = {
    queued: '等待运行', starting: '准备中', competitor_discovery: '采集竞品候选', competitor_analysis: '竞品AI筛选',
    comment_collection: '采集竞品评论', lead_analysis: '客户意向分析', message_sending: '自动私信',
    traffic_creating: '创建引流批次', create_traffic_run: '创建引流批次', traffic_waiting: '等待浏览器', waiting_browser: '等待浏览器',
    traffic_running: '执行引流', traffic_execution: '执行引流',
    completed: '已完成', partial: '部分完成', failed: '失败', cancelled: '已停止', skipped: '已跳过', pending: '待执行', succeeded: '已完成',
  }
  return labels[stage] || stage || '—'
}

function relatedJobIds(context: Dict) {
  return Object.entries(context || {}).filter(([key, value]) => key.endsWith('_task_id') || key.endsWith('_runtime_job_id') || key.endsWith('_task_ids')).flatMap(([, value]) => Array.isArray(value) ? value : [value]).filter(Boolean).map(String)
}

function timeValue(value: string) {
  return value || '09:00'
}

function shortId(value: string) {
  return value.length > 10 ? value.slice(0, 8) : value
}
