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
import { ChatDotRound, Clock, Promotion } from '@element-plus/icons-vue'

import { api } from '../shared/api'
import type { Dict } from '../shared/types'


const weekdays = [
  [1, '周一'], [2, '周二'], [3, '周三'], [4, '周四'], [5, '周五'], [6, '周六'], [7, '周日'],
]

const emptyDraft = (planType = 'keyword_lead'): Dict => ({
  id: '',
  name: planType === 'message' ? '自动私信' : '关键词自动获客',
  plan_type: planType,
  weekdays: [1, 2, 3, 4, 5],
  run_time: '09:00',
  enabled: false,
  config: planType === 'message'
    ? {
        platform: 'dy', keyword_scope: 'all', keywords: [], count: 20,
        script_mode: 'ai', fixed_script: '', interval_min_seconds: 30, interval_max_seconds: 60,
      }
    : {
        platform: 'dy', keywords: [], keyword_count: 1, discovery_content_count: 20,
        competitor_limit: 30, competitor_content_count: 10, comment_count: 50,
        collect_sub_comments: false, auto_analyze_leads: true,
      },
})


export default defineComponent({
  name: 'AutomationPlanPage',
  props: { refreshSeq: { type: Number, default: 0 } },
  setup(props) {
    const route = useRoute()
    const router = useRouter()
    const plans = ref<Dict[]>([])
    const runs = ref<Dict[]>([])
    const summary = ref<Dict>({})
    const limits = ref<Dict>({})
    const limitDraft = reactive({ daily_limit: 100, hourly_limit: 40 })
    const editorOpen = ref(false)
    const runDetailOpen = ref(false)
    const saving = ref(false)
    const selectedRun = ref<Dict>({})
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
        const [planResponse, runResponse, limitResponse] = await Promise.all([
          api.get('/automation/plans'),
          api.get('/automation/runs', { params: { page: 1, page_size: 50 } }),
          api.get('/automation/message-limits'),
        ])
        plans.value = planResponse.data.items || []
        summary.value = planResponse.data.summary || {}
        runs.value = runResponse.data.items || []
        limits.value = limitResponse.data || {}
        limitDraft.daily_limit = Number(limits.value.daily_limit || 100)
        limitDraft.hourly_limit = Number(limits.value.hourly_limit || 40)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '自动化计划加载失败')
      }
    }

    function openFromRoute() {
      if (String(route.query.create || '') !== '1') return
      openEditor(String(route.query.type || 'keyword_lead'))
      router.replace({ path: '/automation-plans', query: {} })
    }

    function openEditor(type: string, plan?: Dict) {
      Object.keys(draft).forEach(key => delete draft[key])
      Object.assign(draft, plan ? JSON.parse(JSON.stringify(plan)) : emptyDraft(type))
      editorOpen.value = true
    }

    function keywordText(config: Dict) {
      return Array.isArray(config.keywords) ? config.keywords.join('\n') : ''
    }

    function setKeywords(config: Dict, value: string) {
      config.keywords = String(value || '').split(/[\n,，]/).map(item => item.trim()).filter(Boolean)
    }

    async function savePlan() {
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
        h(ElCol, { span: 12 }, () => h(ElCard, { shadow: 'hover', class: 'automation-task-card' }, () => [
          h('div', { class: 'automation-card-icon lead' }, [h(Promotion)]),
          h('div', { class: 'automation-card-content' }, [
            h('h3', '关键词自动获客'),
            h('p', '按优先级选择关键词，依次采竞品、AI筛选、采评论并可选分析客户。'),
            h('div', { class: 'automation-flow' }, '选择关键词 → 采竞品 → AI筛选 → 采评论 → 客户分析'),
          ]),
          h(ElButton, { type: 'primary', onClick: () => openEditor('keyword_lead') }, () => '新建获客计划'),
        ])),
        h(ElCol, { span: 12 }, () => h(ElCard, { shadow: 'hover', class: 'automation-task-card' }, () => [
          h('div', { class: 'automation-card-icon message' }, [h(ChatDotRound)]),
          h('div', { class: 'automation-card-content' }, [
            h('h3', '自动私信'),
            h('p', '独立消化新旧积压客户，高意向优先，同意向下等待最久的客户优先。'),
            h('div', { class: 'automation-flow' }, '筛选积压客户 → 占用额度 → 按间隔逐个私信'),
          ]),
          h(ElButton, { type: 'primary', onClick: () => openEditor('message') }, () => '新建私信计划'),
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
        h('div', { class: 'automation-section-head' }, [h('div', [h('h2', '计划列表'), h('p', '一天需要多次执行时，创建多个开始时间不同的计划。')])]),
        h(ElTable, { data: plans.value, stripe: true, emptyText: '还没有自动化计划' }, () => [
          h(ElTableColumn, { label: '计划', minWidth: 180 }, { default: ({ row }: Dict) => h('div', [h('strong', row.name), h('small', { class: 'table-subtext' }, planTypeLabel(row.plan_type))]) }),
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
          h(ElTableColumn, { label: '进度', width: 150 }, { default: ({ row }: Dict) => row.plan_type === 'message'
            ? h('span', `${row.message_success_count || 0} 成功 / ${row.message_attempted_count || 0} 尝试`)
            : h(ElProgress, { percentage: planProgress(row), strokeWidth: 8 }) }),
          h(ElTableColumn, { label: '结果', minWidth: 180 }, { default: ({ row }: Dict) => h('span', row.error || `${row.success_count || 0} 成功，${row.failed_count || 0} 失败`) }),
          h(ElTableColumn, { label: '操作', width: 150, fixed: 'right' }, { default: ({ row }: Dict) => h('div', { class: 'table-actions' }, [
            h(ElButton, { text: true, type: 'primary', onClick: () => showRun(row) }, () => '详情'),
            ['queued', 'running'].includes(row.status) ? h(ElButton, { text: true, type: 'danger', onClick: () => cancelRun(row) }, () => '停止') : null,
          ]) }),
        ]),
      ])
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
            : '选择新旧积压客户 → 高意向优先排序 → 占用统一额度 → 逐个私信'),
          ...(draft.plan_type === 'keyword_lead' ? renderKeywordFields() : renderMessageFields()),
        ]),
        footer: () => h('div', [h(ElButton, { onClick: () => editorOpen.value = false }, () => '取消'), h(ElButton, { type: 'primary', loading: saving.value, onClick: savePlan }, () => '保存计划')]),
      })
    }

    function renderKeywordFields() {
      const config = draft.config as Dict
      return [
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
        h(ElFormItem, { label: '客户意向分析' }, () => h(ElSwitch, { modelValue: config.auto_analyze_leads, activeText: '采集后自动分析', inactiveText: '仅保留待筛选客户', 'onUpdate:modelValue': (value: string | number | boolean) => config.auto_analyze_leads = Boolean(value) })),
        h(ElAlert, { title: '竞品 AI 筛选固定执行；非竞品和非客户保留记录，但不会进入后续流程。此计划不会直接触发私信。', type: 'info', showIcon: true, closable: false }),
      ]
    }

    function renderMessageFields() {
      const config = draft.config as Dict
      return [
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

    function numberField(label: string, target: Dict, key: string, min: number, max: number, span = 8) {
      return h(ElCol, { span }, () => h(ElFormItem, { label }, () => h(ElInputNumber, { modelValue: target[key], min, max, controlsPosition: 'right', 'onUpdate:modelValue': (value: number | undefined) => { if (value !== undefined) target[key] = value } })))
    }

    function renderRunDetail() {
      const run = selectedRun.value
      return h(ElDialog, { modelValue: runDetailOpen.value, title: `执行详情 · ${run.plan_name || ''}`, width: '820px', 'onUpdate:modelValue': (value: boolean) => runDetailOpen.value = value }, { default: () => [
        h('div', { class: 'automation-run-summary' }, [statusTag(run.status), h('span', `阶段：${stageLabel(run.current_stage)}`), h('span', `成功 ${run.success_count || 0}`), h('span', `失败 ${run.failed_count || 0}`), h('span', `跳过 ${run.skipped_count || 0}`)]),
        run.error ? h(ElAlert, { title: run.error, type: run.status === 'partial' ? 'warning' : 'error', showIcon: true, closable: false }) : null,
        run.plan_type === 'message' ? h('div', { class: 'automation-message-result' }, `私信批次 ${run.message_batch_id || '—'}：尝试 ${run.message_attempted_count || 0}，成功 ${run.message_success_count || 0}`) : null,
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
        h('div', [h('div', { class: 'automation-eyebrow' }, [h(Clock), h('span', '无人值守获客')]), h('h1', '自动化计划'), h('p', '按指定星期和时间运行固定业务流程。软件未运行时错过的计划会直接跳过。')]),
        h('div', { class: 'automation-metrics' }, [metric('已启用计划', summary.value.enabled || 0, 'green'), metric('下一个计划', summary.value.next_run_at || '暂无', 'blue'), metric('正在运行', summary.value.running || 0, 'blue'), metric('今日失败', summary.value.failed_today || 0, 'red')]),
      ]),
      renderTaskCards(),
      renderLimits(),
      renderPlans(),
      renderRuns(),
      renderEditor(),
      renderRunDetail(),
    ])
  },
})


function planTypeLabel(type: string) {
  return type === 'message' ? '自动私信' : '关键词自动获客'
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
  return `${(config.keywords || []).length}个关键词 · 每次${config.keyword_count || 1}个`
}

function statusTag(status: string) {
  const labels: Dict = { queued: '排队中', running: '运行中', completed: '完成', partial: '部分完成', failed: '失败', skipped: '已跳过', cancelled: '已停止', pending: '待执行', succeeded: '成功' }
  const types: Dict = { completed: 'success', succeeded: 'success', partial: 'warning', failed: 'danger', cancelled: 'info', skipped: 'info', running: 'primary' }
  return h(ElTag, { type: types[status] || 'info', effect: status === 'running' ? 'dark' : 'light' }, () => labels[status] || status || '—')
}

function stageLabel(stage: string) {
  const labels: Dict = { queued: '等待运行', starting: '准备中', competitor_discovery: '采集竞品候选', competitor_analysis: '竞品AI筛选', comment_collection: '采集竞品评论', lead_analysis: '客户意向分析', message_sending: '自动私信', completed: '已完成', partial: '部分完成', failed: '失败', cancelled: '已停止', skipped: '已跳过', pending: '待执行', succeeded: '已完成' }
  return labels[stage] || stage || '—'
}

function relatedJobIds(context: Dict) {
  return Object.entries(context || {}).filter(([key, value]) => key.endsWith('_task_id') || key.endsWith('_runtime_job_id') || key.endsWith('_task_ids')).flatMap(([, value]) => Array.isArray(value) ? value : [value]).filter(Boolean).map(String)
}

function timeValue(value: string) {
  return value || '09:00'
}
