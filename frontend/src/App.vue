<template>
  <el-container class="shell">
    <el-aside class="sidebar" width="236px">
      <div class="brand">
        <div class="brand-mark">AI</div>
        <div>
          <strong>AI拓客工具</strong>
          <span>采集 · 筛选 · 跟进</span>
        </div>
      </div>
      <el-menu :default-active="activeView" class="nav" @select="activeView = $event">
        <el-menu-item index="tasks"><el-icon><Operation /></el-icon><span>任务管理</span></el-menu-item>
        <el-menu-item index="overview"><el-icon><Share /></el-icon><span>总览树</span></el-menu-item>
        <el-menu-item index="ai"><el-icon><MagicStick /></el-icon><span>AI分析</span></el-menu-item>
        <el-menu-item index="logs"><el-icon><Tickets /></el-icon><span>任务与日志</span></el-menu-item>
        <el-menu-item index="tables"><el-icon><Grid /></el-icon><span>数据表</span></el-menu-item>
        <el-menu-item index="settings"><el-icon><Setting /></el-icon><span>设置</span></el-menu-item>
      </el-menu>
      <div class="sidebar-status">
        <small>当前流程</small>
        <strong>{{ workflowHint }}</strong>
      </div>
    </el-aside>

    <el-container>
      <el-header class="topbar">
        <div>
          <h1>{{ viewTitle }}</h1>
          <p>{{ viewSubtitle }}</p>
        </div>
        <div class="topbar-actions">
          <el-tag :type="envReady ? 'success' : 'warning'" effect="light">
            {{ envReady ? '环境就绪' : '需要检查环境' }}
          </el-tag>
          <el-button :icon="Refresh" @click="refreshAll">刷新</el-button>
          <el-button type="primary" :icon="Plus" @click="activeView = 'tasks'">新建任务</el-button>
        </div>
      </el-header>

      <el-main class="main">
        <section class="workflow-strip">
          <div v-for="step in workflowSteps" :key="step.title" class="workflow-step" :class="{ active: step.view === activeView }">
            <span>{{ step.index }}</span>
            <div>
              <strong>{{ step.title }}</strong>
              <small>{{ step.note }}</small>
            </div>
          </div>
        </section>

        <TaskView
          v-if="activeView === 'tasks'"
          :tasks="tasks"
          :settings="settings"
          @create-task="createTask"
          @open-logs="openTaskLogs"
        />
        <OverviewView v-else-if="activeView === 'overview'" :tree="overviewTree" />
        <AiView v-else-if="activeView === 'ai'" :jobs="aiJobs" :lead-rows="leadRows" :competitor-rows="competitorRows" @create-job="createAiJob" @retry-job="retryAiJob" />
        <LogsView v-else-if="activeView === 'logs'" :tasks="tasks" :selected-task="selectedTask || undefined" @select-task="selectTask" @archive-task="archiveTask" @delete-task="deleteTask" />
        <TablesView
          v-else-if="activeView === 'tables'"
          :library="activeLibrary"
          :rows="tableRows"
          :loading="tableLoading"
          @change-library="changeLibrary"
          @update-row="updateRow"
          @delete-row="deleteRow"
          @analyze-row="analyzeTableRow"
          @enrich-profile="enrichProfile"
        />
        <SettingsView v-else :settings="settings" :env="env" @save="saveSettings" @check-env="checkEnv" @clear-data="clearAllData" />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import axios from 'axios'
import {
  Check,
  Close,
  CopyDocument,
  Delete,
  Grid,
  MagicStick,
  Operation,
  Plus,
  Refresh,
  Setting,
  Share,
  Tickets,
  VideoPlay
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const api = axios.create({ baseURL: '/api' })

type Dict = Record<string, any>

const activeView = ref('tasks')
const activeLibrary = ref('contents')
const tasks = ref<Dict[]>([])
const tableRows = ref<Dict[]>([])
const tableLoading = ref(false)
const overviewTree = ref<Dict[]>([])
const aiJobs = ref<Dict[]>([])
const selectedTask = ref<Dict | null>(null)
const leadRows = ref<Dict[]>([])
const competitorRows = ref<Dict[]>([])
const settings = ref<Dict>({})
const env = ref<Dict>({})

const workflowSteps = [
  { index: '01', title: '配置画像', note: 'AI模型、ICP、默认采集参数', view: 'settings' },
  { index: '02', title: '创建任务', note: '四种模式，一次选择', view: 'tasks' },
  { index: '03', title: '观察入库', note: '日志、数量、失败原因', view: 'logs' },
  { index: '04', title: '理解来源', note: '数据表与总览树交叉查看', view: 'tables' },
  { index: '05', title: 'AI筛选跟进', note: '批量分析、私信话术、状态流转', view: 'ai' }
]

const viewMeta: Record<string, { title: string; subtitle: string; hint: string }> = {
  tasks: { title: '任务管理', subtitle: '选择拓客模式，生成 MediaCrawler 参数，并自动导入项目库。', hint: '选择模式后直接开始采集' },
  overview: { title: '总览树', subtitle: '按平台、关键词、账号、内容和客户查看证据链。', hint: '从业务关系理解数据' },
  ai: { title: 'AI分析', subtitle: '集中处理竞品筛选、目标客户筛选、失败重试和私信话术。', hint: '把线索变成可跟进客户' },
  logs: { title: '任务与日志', subtitle: '查看任务运行状态、控制台输出、归档和硬删除。', hint: '先确认任务是否正确完成' },
  tables: { title: '数据表', subtitle: '维护内容、评论、竞品、线索和目标客户。', hint: '直接处理具体数据' },
  settings: { title: '设置', subtitle: '配置 AI 模型、MediaCrawler 路径、自动化和 ICP 画像。', hint: '开始前先把基础环境配好' }
}

const viewTitle = computed(() => viewMeta[activeView.value].title)
const viewSubtitle = computed(() => viewMeta[activeView.value].subtitle)
const workflowHint = computed(() => viewMeta[activeView.value].hint)
const envReady = computed(() => Boolean(env.value?.media_crawler_path?.ok && env.value?.media_crawler_db?.ok))

async function refreshAll() {
  // 首页各面板独立加载，单个接口失败时不阻塞其它工作区。
  await Promise.allSettled([loadTasks(), loadSettings(), checkEnv(), loadAiJobs(), loadOverview(), loadTable(activeLibrary.value)])
}

async function loadTasks() {
  const { data } = await api.get('/tasks')
  tasks.value = data
  if (!selectedTask.value && data.length) selectedTask.value = await fetchTask(data[0].id)
}

async function fetchTask(id: string) {
  const { data } = await api.get(`/tasks/${id}`)
  return data
}

async function loadSettings() {
  const { data } = await api.get('/settings')
  settings.value = data
}

async function checkEnv() {
  const { data } = await api.get('/settings/env-check')
  env.value = data
}

async function loadAiJobs() {
  const { data } = await api.get('/ai/jobs')
  aiJobs.value = data
}

async function loadOverview() {
  const { data } = await api.get('/overview/tree')
  overviewTree.value = data
}

async function loadTable(library: string) {
  tableLoading.value = true
  try {
    const { data } = await api.get(`/tables/${library}`)
    tableRows.value = data.rows
    if (library === 'lead_customers') leadRows.value = data.rows
    if (library === 'competitor_candidates') competitorRows.value = data.rows
  } finally {
    tableLoading.value = false
  }
}

async function changeLibrary(library: string) {
  activeLibrary.value = library
  await loadTable(library)
}

async function createTask(payload: Dict) {
  try {
    const { data } = await api.post('/tasks', payload)
    ElMessage.success(`任务 ${data.id} 已创建`)
    await loadTasks()
    selectedTask.value = await fetchTask(data.id)
    activeView.value = 'logs'
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '任务创建失败')
  }
}

async function openTaskLogs(id: string) {
  selectedTask.value = await fetchTask(id)
  activeView.value = 'logs'
}

async function selectTask(id: string) {
  selectedTask.value = await fetchTask(id)
}

async function archiveTask(id: string) {
  await api.post(`/tasks/${id}/archive`)
  ElMessage.success('任务已归档')
  await loadTasks()
}

async function deleteTask(id: string) {
  await ElMessageBox.confirm('任务删除会同步删除项目库和 MediaCrawler 底层映射数据。确认继续？', '硬删除确认', { type: 'warning' })
  await api.delete(`/tasks/${id}`)
  ElMessage.success('任务已硬删除')
  selectedTask.value = null
  await loadTasks()
}

async function updateRow(library: string, row: Dict) {
  await api.patch(`/tables/${library}/${row.id}`, { values: row })
  ElMessage.success('已保存')
  await loadTable(library)
}

async function deleteRow(library: string, row: Dict, hard?: boolean) {
  const message = library === 'target_customers' && !hard ? '目标客户会先隐藏并记录状态事件。确认删除？' : '此操作会同步删除底层映射数据。确认继续？'
  await ElMessageBox.confirm(message, '删除确认', { type: 'warning' })
  await api.delete(`/tables/${library}/${row.id}`, { params: { hard } })
  ElMessage.success('删除完成')
  await loadTable(library)
}

async function createAiJob(targetType: string, targetId: number) {
  await api.post('/ai/jobs', { target_type: targetType, target_id: targetId, run_now: true })
  ElMessage.success('AI分析完成')
  await Promise.all([loadAiJobs(), loadTable(activeLibrary.value)])
}

async function retryAiJob(jobId: string) {
  await api.post(`/ai/jobs/${jobId}/retry`)
  ElMessage.success('重试完成')
  await loadAiJobs()
}

async function analyzeTableRow(library: string, row: Dict) {
  if (library === 'competitor_candidates') await createAiJob('competitor', row.id)
  if (library === 'lead_customers') await createAiJob('lead', row.id)
}

async function enrichProfile(library: string, row: Dict) {
  const accountId = row.account_id || row.id
  if (!accountId) {
    ElMessage.error('当前记录缺少账号ID，无法补资料')
    return
  }
  try {
    const { data } = await api.post(`/accounts/${accountId}/profile-enrichment`)
    ElMessage.success(`补资料任务 ${data.id} 已创建`)
    await loadTasks()
    selectedTask.value = await fetchTask(data.id)
    activeView.value = 'logs'
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '补资料任务创建失败')
  }
}

async function saveSettings(values: Dict) {
  const { data } = await api.put('/settings', { values })
  settings.value = data
  ElMessage.success('设置已保存')
  await checkEnv()
}

async function clearAllData() {
  let value = ''
  try {
    const result = await ElMessageBox.prompt(
      '这会清空项目业务库、任务日志、AI结果、证据链，并清空当前设置指向的 MediaCrawler SQLite 所有业务表内容。数据库文件和设置项会保留。请输入“清空所有数据”确认。',
      '清空所有数据',
      {
        confirmButtonText: '清空',
        cancelButtonText: '取消',
        inputPlaceholder: '清空所有数据',
        inputValidator: (input) => input === '清空所有数据' || '请输入：清空所有数据',
        type: 'warning'
      }
    )
    value = result.value
  } catch (error) {
    // 取消或关闭弹窗是正常路径，不请求清空接口。
    if (error === 'cancel' || error === 'close') return
    throw error
  }
  const { data } = await api.post('/settings/clear-data', { confirm: value })
  ElMessage.success(`已清空项目库 ${data.project.rows} 行，MediaCrawler ${data.media_crawler.rows} 行`)
  selectedTask.value = null
  overviewTree.value = []
  tableRows.value = []
  aiJobs.value = []
  leadRows.value = []
  competitorRows.value = []
  await refreshAll()
}

onMounted(refreshAll)

const splitWidths = reactive<Record<string, number>>({})

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}

const SplitPane = defineComponent({
  props: {
    storageKey: { type: String, required: true },
    side: { type: String, default: 'right' },
    defaultSideWidth: { type: Number, default: 360 },
    minSideWidth: { type: Number, default: 240 },
    maxSideWidth: { type: Number, default: 680 }
  },
  setup(props, { slots }) {
    if (!splitWidths[props.storageKey]) splitWidths[props.storageKey] = props.defaultSideWidth
    const dragging = ref(false)
    let startX = 0
    let startWidth = 0

    function setWidth(nextWidth: number) {
      splitWidths[props.storageKey] = clamp(nextWidth, props.minSideWidth, props.maxSideWidth)
    }

    function stopDrag() {
      dragging.value = false
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', stopDrag)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    function onPointerMove(event: PointerEvent) {
      const deltaX = event.clientX - startX
      setWidth(props.side === 'left' ? startWidth + deltaX : startWidth - deltaX)
    }

    function startDrag(event: PointerEvent) {
      event.preventDefault()
      dragging.value = true
      startX = event.clientX
      startWidth = splitWidths[props.storageKey]
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      window.addEventListener('pointermove', onPointerMove)
      window.addEventListener('pointerup', stopDrag)
    }

    function onKeydown(event: KeyboardEvent) {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
      event.preventDefault()
      const direction = event.key === 'ArrowRight' ? 1 : -1
      const step = event.shiftKey ? 40 : 16
      setWidth(splitWidths[props.storageKey] + (props.side === 'left' ? direction * step : -direction * step))
    }

    onBeforeUnmount(stopDrag)

    return () => {
      const sideWidth = splitWidths[props.storageKey]
      const gridTemplateColumns = props.side === 'left'
        ? `${sideWidth}px 10px minmax(0, 1fr)`
        : `minmax(0, 1fr) 10px ${sideWidth}px`
      const mainSlot = slots.default?.() || []
      const sideSlot = slots.side?.() || []
      const resizer = h('button', {
        class: 'split-resizer',
        type: 'button',
        title: '拖动调整左右宽度',
        'aria-label': '拖动调整左右宽度',
        onPointerdown: startDrag,
        onKeydown
      })
      return h('div', { class: ['split-pane', `side-${props.side}`, dragging.value ? 'dragging' : ''], style: { gridTemplateColumns } },
        props.side === 'left' ? [...sideSlot, resizer, ...mainSlot] : [...mainSlot, resizer, ...sideSlot]
      )
    }
  }
})

function splitTagText(text: string) {
  return text.split(/[\n\r,，;；]+/).map(item => item.trim()).filter(Boolean)
}

function mergeTags(current: string[], incoming: string[]) {
  const next = [...current]
  incoming.forEach(item => {
    if (!next.includes(item)) next.push(item)
  })
  return next
}

function joinTags(tags: string[]) {
  return tags.map(item => item.trim()).filter(Boolean).join(',')
}

const TagInput = defineComponent({
  props: {
    modelValue: { type: Array, required: true },
    disabled: { type: Boolean, default: false },
    placeholder: { type: String, default: '' }
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    const draft = ref('')
    const inputRef = ref<HTMLInputElement | null>(null)
    const currentTags = () => (props.modelValue as string[]).map(item => String(item))
    function updateTags(tags: string[]) {
      emit('update:modelValue', tags)
    }
    function commitDraft() {
      const incoming = splitTagText(draft.value)
      if (!incoming.length) return
      updateTags(mergeTags(currentTags(), incoming))
      draft.value = ''
    }
    function removeTag(index: number) {
      const next = currentTags()
      next.splice(index, 1)
      updateTags(next)
    }
    function onInput(event: Event) {
      const value = (event.target as HTMLInputElement).value
      if (/[\n\r,，;；]/.test(value)) {
        updateTags(mergeTags(currentTags(), splitTagText(value)))
        draft.value = ''
        return
      }
      draft.value = value
    }
    function onPaste(event: ClipboardEvent) {
      const text = event.clipboardData?.getData('text') || ''
      if (!/[\n\r,，;；]/.test(text)) return
      event.preventDefault()
      updateTags(mergeTags(currentTags(), splitTagText(text)))
      draft.value = ''
    }
    function onKeydown(event: KeyboardEvent) {
      if (event.key === 'Enter') {
        event.preventDefault()
        commitDraft()
      }
      if (event.key === 'Backspace' && !draft.value && currentTags().length) {
        removeTag(currentTags().length - 1)
      }
    }
    return () => {
      const tags = currentTags()
      return h('div', {
        class: ['tag-input', props.disabled ? 'disabled' : ''],
        onClick: () => inputRef.value?.focus()
      }, [
        ...tags.map((tag, index) => h('span', { class: 'tag-chip' }, [
          h('span', { class: 'tag-chip-text' }, tag),
          h('button', {
            type: 'button',
            class: 'tag-remove',
            title: `移除 ${tag}`,
            disabled: props.disabled,
            onClick: (event: MouseEvent) => {
              event.stopPropagation()
              removeTag(index)
            }
          }, [h(Close, { class: 'tag-remove-icon' })])
        ])),
        h('input', {
          ref: inputRef,
          value: draft.value,
          disabled: props.disabled,
          placeholder: tags.length ? '' : props.placeholder,
          onInput,
          onPaste,
          onKeydown,
          onBlur: commitDraft
        })
      ])
    }
  }
})

const icpFields = [
  { key: 'product', label: '产品/服务', placeholder: '例如：AI客服、获客工具' },
  { key: 'industry', label: '目标行业', placeholder: '例如：跨境电商、教育培训' },
  { key: 'roles', label: '目标角色', placeholder: '例如：老板、运营负责人、销售主管' },
  { key: 'pain_points', label: '典型痛点', placeholder: '用户常见问题、需求或抱怨', multiline: true },
  { key: 'high_intent_words', label: '高意向词', placeholder: '例如：求推荐、怎么选、多少钱', multiline: true },
  { key: 'value_proposition', label: '价值主张', placeholder: '产品能解决什么问题，适合什么客户', multiline: true },
  { key: 'excluded_audience', label: '排除人群', placeholder: '不需要跟进的人群或场景', multiline: true }
]

function defaultIcpProfile() {
  return Object.fromEntries(icpFields.map(field => [field.key, '']))
}

function normalizeIcpProfile(value: any) {
  let source = value
  if (typeof value === 'string') {
    try { source = JSON.parse(value) } catch { source = {} }
  }
  return { ...defaultIcpProfile(), ...(source && typeof source === 'object' ? source : {}) }
}

function buildIcpPayload(value: Dict) {
  const result: Dict = {}
  icpFields.forEach(field => {
    result[field.key] = String(value?.[field.key] || '').trim()
  })
  return result
}

const TaskView = defineComponent({
  props: { tasks: { type: Array, required: true }, settings: { type: Object, required: true } },
  emits: ['create-task', 'open-logs'],
  setup(props, { emit }) {
    const modes = [
      { key: 'competitor_discovery', title: '竞品账号采集', note: '关键词找竞品候选，再用AI确认', badge: '找账号' },
      { key: 'competitor_crawl', title: '竞品账号爬取', note: '爬评论区，把评论用户转为线索', badge: '找线索' },
      { key: 'demand_content', title: '找需求内容', note: '关键词找吐槽/需求内容，作者进入客户池', badge: '找需求' },
      { key: 'own_account', title: '自家账号互动', note: '监控自家评论区，筛出高意向用户', badge: '自有流量' }
    ]
    const form = reactive({
      mode: 'competitor_discovery',
      platform: 'dy',
      login_type: 'qrcode',
      keyword_tags: [] as string[],
      creator_id_tags: [] as string[],
      specified_id_tags: [] as string[],
      content_count: 20,
      comment_count: 20,
      max_concurrency: 1,
      collect_comments: false,
      collect_sub_comments: false,
      headless: false,
      tcp_mode: true,
      execute_crawler: true
    })
    const modeNeedsCreator = computed(() => ['competitor_crawl', 'own_account'].includes(form.mode))
    const modeUsesKeywords = computed(() => ['competitor_discovery', 'demand_content'].includes(form.mode))
    function applyMode(modeKey: string) {
      form.mode = modeKey
      form.collect_comments = ['competitor_crawl', 'own_account'].includes(modeKey)
      form.collect_sub_comments = form.collect_comments
      if (['competitor_discovery', 'demand_content'].includes(modeKey)) {
        form.creator_id_tags = []
        form.specified_id_tags = []
      } else {
        form.keyword_tags = []
      }
    }
    function buildTaskPayload() {
      const payload = {
        mode: form.mode,
        platform: form.platform,
        login_type: form.login_type,
        keywords: joinTags(form.keyword_tags),
        creator_id: joinTags(form.creator_id_tags),
        specified_id: joinTags(form.specified_id_tags),
        content_count: form.content_count,
        comment_count: form.comment_count,
        max_concurrency: form.max_concurrency,
        collect_comments: form.collect_comments,
        collect_sub_comments: form.collect_sub_comments,
        headless: form.headless,
        tcp_mode: form.tcp_mode,
        execute_crawler: form.execute_crawler
      }
      if (['competitor_discovery', 'demand_content'].includes(payload.mode)) {
        payload.creator_id = ''
        payload.specified_id = ''
      } else if (payload.specified_id) {
        payload.keywords = ''
        payload.creator_id = ''
      } else {
        payload.keywords = ''
      }
      return payload
    }
    function submit() {
      const payload = buildTaskPayload()
      if (modeUsesKeywords.value && !payload.keywords) {
        ElMessage.error('搜索型任务必须填写关键词')
        return
      }
      if (!modeUsesKeywords.value && !payload.creator_id && !payload.specified_id) {
        ElMessage.error('账号/详情采集任务必须填写创作者主页/ID或指定内容ID')
        return
      }
      emit('create-task', payload)
    }
    return () => h(SplitPane, { storageKey: 'tasks', side: 'right', defaultSideWidth: 360 }, {
      default: () => [
      h('section', { class: 'pane primary-pane' }, [
        h('div', { class: 'section-title' }, [h('h2', '选择拓客模式'), h('span', '先选目标，再填必要参数')]),
        h('div', { class: 'mode-grid' }, modes.map(mode => h('button', {
          class: ['mode-option', form.mode === mode.key ? 'selected' : ''],
          onClick: () => applyMode(mode.key)
        }, [h('small', mode.badge), h('strong', mode.title), h('span', mode.note)]))),
        h('div', { class: 'form-grid' }, [
          h('label', ['平台', h('select', { value: form.platform, onChange: (event: Event) => form.platform = (event.target as HTMLSelectElement).value }, [
            h('option', { value: 'dy' }, '抖音'),
            h('option', { value: 'xhs' }, '小红书'),
            h('option', { value: 'ks' }, '快手')
          ])]),
          h('label', ['登录方式', h('select', { value: form.login_type, onChange: (event: Event) => form.login_type = (event.target as HTMLSelectElement).value }, [
            h('option', { value: 'qrcode' }, '二维码'),
            h('option', { value: 'phone' }, '手机号'),
            h('option', { value: 'cookie' }, 'Cookie')
          ])]),
          h('label', { class: 'form-field field-full' }, ['关键词', h(TagInput, {
            modelValue: form.keyword_tags,
            disabled: !modeUsesKeywords.value,
            placeholder: modeUsesKeywords.value ? '输入关键词后按回车，例如：AI客服' : '账号/详情任务不使用关键词',
            'onUpdate:modelValue': (value: string[]) => form.keyword_tags = value
          })]),
          h('label', { class: 'form-field field-full' }, ['创作者主页/ID', h(TagInput, {
            modelValue: form.creator_id_tags,
            disabled: modeUsesKeywords.value,
            placeholder: modeNeedsCreator.value ? '输入账号主页或ID后按回车' : '详情任务可不填',
            'onUpdate:modelValue': (value: string[]) => form.creator_id_tags = value
          })]),
          h('label', { class: 'form-field field-full' }, ['指定内容ID/链接', h(TagInput, {
            modelValue: form.specified_id_tags,
            disabled: modeUsesKeywords.value,
            placeholder: modeUsesKeywords.value ? '搜索任务不使用内容ID' : '输入内容ID或链接后按回车',
            'onUpdate:modelValue': (value: string[]) => form.specified_id_tags = value
          })]),
          h('label', ['内容数量', h('input', { type: 'number', value: form.content_count, min: 1, onInput: (event: Event) => form.content_count = Number((event.target as HTMLInputElement).value) })]),
          h('label', ['单条评论数', h('input', { type: 'number', value: form.comment_count, min: 0, onInput: (event: Event) => form.comment_count = Number((event.target as HTMLInputElement).value) })]),
          h('label', ['并发数', h('input', { type: 'number', value: form.max_concurrency, min: 1, max: 10, onInput: (event: Event) => form.max_concurrency = Number((event.target as HTMLInputElement).value) })])
        ]),
        h('div', { class: 'toggles' }, [
          h('label', [h('input', { type: 'checkbox', checked: form.collect_comments, onChange: (event: Event) => form.collect_comments = (event.target as HTMLInputElement).checked }), '采集评论']),
          h('label', [h('input', { type: 'checkbox', checked: form.collect_sub_comments, onChange: (event: Event) => form.collect_sub_comments = (event.target as HTMLInputElement).checked }), '二级评论']),
          h('label', [h('input', { type: 'checkbox', checked: form.headless, onChange: (event: Event) => form.headless = (event.target as HTMLInputElement).checked }), '无头模式']),
          h('label', [h('input', { type: 'checkbox', checked: form.execute_crawler, onChange: (event: Event) => form.execute_crawler = (event.target as HTMLInputElement).checked }), '立即运行'])
        ]),
        h('div', { class: 'action-row' }, [
          h('button', { class: 'primary-action', onClick: submit }, [h(VideoPlay, { class: 'inline-icon' }), '开始采集并导入'])
        ])
      ])
      ],
      side: () => [
      h('aside', { class: 'pane side-pane' }, [
        h('div', { class: 'section-title' }, [h('h2', '最近任务'), h('span', '确认采集是否跑通')]),
        h('div', { class: 'task-list' }, (props.tasks as Dict[]).slice(0, 8).map(task => h('button', { class: 'task-row', onClick: () => emit('open-logs', task.id) }, [
          h('strong', `${task.id} · ${task.name}`),
          h('span', `${task.platform} / ${task.mode}`),
          h('em', { class: `status ${task.status}` }, task.status)
        ])))
      ])
      ]
    })
  }
})

const OverviewView = defineComponent({
  props: { tree: { type: Array, required: true } },
  setup(props) {
    const expanded = reactive<Record<string, boolean>>({})
    const isExpanded = (node: Dict) => expanded[node.id] ?? false
    const toggle = (node: Dict) => {
      expanded[node.id] = !isExpanded(node)
    }
    return () => h('section', { class: 'pane overview-pane' }, [
      h('div', { class: 'section-title' }, [h('h2', '关系总览'), h('span', '平台 / 关键词 / 账号层级表')]),
      (props.tree as Dict[]).length
        ? h('div', { class: 'overview-table' }, (props.tree as Dict[]).flatMap(node => renderOverviewRow(node, 0, isExpanded, toggle)))
        : h('div', { class: 'empty-state' }, '暂无总览数据，请先创建采集任务')
    ])
  }
})

function renderOverviewRow(node: Dict, level: number, isExpanded: (node: Dict) => boolean, toggle: (node: Dict) => void): any[] {
  const children = node.children || []
  const opened = isExpanded(node)
  const rows = [
    h('div', { class: ['overview-row', `level-${level}`, node.kind] }, [
      h('div', { class: 'overview-main', style: { paddingLeft: `${level * 34}px` } }, [
        children.length
          ? h('button', { class: 'outline-button', onClick: () => toggle(node) }, opened ? '收起' : '展开')
          : h('span', { class: 'overview-spacer' }),
      h('div', { class: 'overview-title' }, [
          renderOverviewTitle(node),
          h('small', overviewSubtitle(node))
        ])
      ]),
      h('div', { class: 'overview-metrics' }, overviewMetricChips(node).map(chip => h('span', { class: 'metric-chip' }, chip)))
    ])
  ]
  if (opened) {
    children.forEach((child: Dict) => rows.push(...renderOverviewRow(child, level + 1, isExpanded, toggle)))
  }
  return rows
}

function renderOverviewTitle(node: Dict) {
  const label = displayOverviewLabel(node)
  if (node.kind === 'account' && node.metrics?.profile_url) {
    return h('a', { class: 'overview-link', href: node.metrics.profile_url, target: '_blank', rel: 'noreferrer' }, label)
  }
  return h('strong', label)
}

function displayOverviewLabel(node: Dict) {
  if (node.kind === 'platform') return platformName(node.label)
  if (node.kind === 'keyword') return `关键词：${node.label}`
  return node.label || `账号 ${node.metrics?.id || ''}`
}

function overviewSubtitle(node: Dict) {
  if (node.kind === 'platform') return node.label
  if (node.kind === 'keyword') return '关键词分组'
  if (node.kind === 'account') return node.metrics?.signature || '暂无主页简介'
  return '账号'
}

function platformName(platform: string) {
  return ({ dy: '抖音', xhs: '小红书', ks: '快手' } as Record<string, string>)[platform] || platform
}

function overviewMetricChips(node: Dict) {
  const metrics = node.metrics || {}
  if (node.kind === 'account') {
    return [
      accountRoleLabel(metrics.account_role) ? `角色 ${accountRoleLabel(metrics.account_role)}` : '',
      metrics.competitor_status ? `竞品 ${metrics.competitor_status}` : '',
      metrics.fans !== null && metrics.fans !== undefined ? `粉丝 ${metrics.fans}` : '',
      `内容 ${metrics.content_count || 0}`,
      `评论 ${metrics.comment_count || 0}`,
      `线索 ${metrics.customer_count || 0}`,
      metrics.latest ? `最近 ${metrics.latest}` : ''
    ].filter(Boolean)
  }
  return [
    `竞品 ${metrics.competitors || 0}`,
    `内容 ${metrics.contents || 0}`,
    `评论 ${metrics.comments || 0}`,
    `线索用户 ${metrics.customers || 0}`,
    metrics.latest ? `最近 ${metrics.latest}` : ''
  ].filter(Boolean)
}

function accountRoleLabel(role: string) {
  return ({
    competitor_candidate: '候选竞品',
    competitor: '竞品',
    own_account: '自家账号',
    lead: '线索账号'
  } as Record<string, string>)[role] || ''
}

const AiView = defineComponent({
  props: { jobs: { type: Array, required: true }, leadRows: { type: Array, required: true }, competitorRows: { type: Array, required: true } },
  emits: ['create-job', 'retry-job'],
  setup(props, { emit }) {
    return () => h(SplitPane, { storageKey: 'ai', side: 'right', defaultSideWidth: 340 }, {
      default: () => [
      h('section', { class: 'pane primary-pane' }, [
        h('div', { class: 'section-title' }, [h('h2', '待分析队列'), h('span', '失败项可直接重试')]),
        h('div', { class: 'metric-row' }, [
          h('div', { class: 'metric-tile accent-green' }, [h('small', '待筛选线索'), h('strong', String((props.leadRows as Dict[]).length))]),
          h('div', { class: 'metric-tile accent-amber' }, [h('small', '竞品候选'), h('strong', String((props.competitorRows as Dict[]).length))]),
          h('div', { class: 'metric-tile accent-blue' }, [h('small', 'AI任务'), h('strong', String((props.jobs as Dict[]).length))])
        ]),
        h('table', { class: 'data-table' }, [
          h('thead', [h('tr', [h('th', '任务'), h('th', '对象'), h('th', '状态'), h('th', '错误'), h('th', '操作')])]),
          h('tbody', (props.jobs as Dict[]).map(job => h('tr', [
            h('td', job.id),
            h('td', `${job.target_type} #${job.target_id}`),
            h('td', h('span', { class: `status ${job.status}` }, job.status)),
            h('td', job.error || '-'),
            h('td', h('button', { class: 'icon-button', onClick: () => emit('retry-job', job.id), title: '重试AI分析' }, [h(Refresh)]))
          ])))
        ])
      ])
      ],
      side: () => [
      h('aside', { class: 'pane side-pane' }, [
        h('div', { class: 'section-title' }, [h('h2', '快速入口'), h('span', '从业务对象发起分析')]),
        h('button', { class: 'wide-action', onClick: () => (props.competitorRows as Dict[])[0] && emit('create-job', 'competitor', (props.competitorRows as Dict[])[0].id) }, [h(MagicStick, { class: 'inline-icon' }), '分析第一个竞品候选']),
        h('button', { class: 'wide-action', onClick: () => (props.leadRows as Dict[])[0] && emit('create-job', 'lead', (props.leadRows as Dict[])[0].id) }, [h(MagicStick, { class: 'inline-icon' }), '分析第一个线索客户'])
      ])
      ]
    })
  }
})

const LogsView = defineComponent({
  props: { tasks: { type: Array, required: true }, selectedTask: { type: Object, default: null } },
  emits: ['select-task', 'archive-task', 'delete-task'],
  setup(props, { emit }) {
    return () => h(SplitPane, { storageKey: 'logs', side: 'right', defaultSideWidth: 390 }, {
      default: () => [
      h('section', { class: 'pane primary-pane log-pane' }, [
        h('div', { class: 'section-title' }, [h('h2', props.selectedTask ? `${props.selectedTask.id} · ${props.selectedTask.name}` : '任务详情'), h('span', props.selectedTask?.status || '请选择任务')]),
        h('div', { class: 'log-console' }, (props.selectedTask?.logs || []).map((log: Dict) => h('p', [h('time', log.created_at), h('span', log.message)])))
      ])
      ],
      side: () => [
      h('aside', { class: 'pane side-pane' }, [
        h('div', { class: 'section-title' }, [h('h2', '任务列表'), h('span', '归档前先看日志')]),
        (props.tasks as Dict[]).map(task => h('button', { class: 'task-row', onClick: () => emit('select-task', task.id) }, [
          h('strong', `${task.id} · ${task.name}`),
          h('span', task.command || task.mode),
          h('em', { class: `status ${task.status}` }, task.status)
        ])),
        props.selectedTask ? h('div', { class: 'button-pair' }, [
          h('button', { onClick: () => emit('archive-task', props.selectedTask.id) }, '归档'),
          h('button', { class: 'danger', onClick: () => emit('delete-task', props.selectedTask.id) }, '硬删除')
        ]) : null
      ])
      ]
    })
  }
})

const TablesView = defineComponent({
  props: { library: { type: String, required: true }, rows: { type: Array, required: true }, loading: { type: Boolean, required: true } },
  emits: ['change-library', 'update-row', 'delete-row', 'analyze-row', 'enrich-profile'],
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
    const headers = ['名称/内容', '状态', '来源任务', '证据/链接', '操作']
    const defaultWidths = [360, 140, 190, 170, 180]
    const widthsByLibrary = reactive<Record<string, number[]>>({})
    let stopColumnResize: (() => void) | null = null
    function columnWidths() {
      if (!widthsByLibrary[props.library]) widthsByLibrary[props.library] = [...defaultWidths]
      return widthsByLibrary[props.library]
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
        h('div', { class: 'library-list' }, libraries.map(([key, label]) => h('button', { class: props.library === key ? 'selected' : '', onClick: () => emit('change-library', key) }, label)))
      ]),
      h('div', { class: 'table-content' }, [
        h('div', { class: 'section-title' }, [h('h2', libraries.find(([key]) => key === props.library)?.[1] || '数据'), h('span', `${(props.rows as Dict[]).length} 条记录`)]),
        h('div', { class: 'table-scroll' }, [
        h('table', { class: 'data-table resizable-table', style: { minWidth: `${columnWidths().reduce((total, width) => total + width, 0)}px` } }, [
          h('colgroup', columnWidths().map(width => h('col', { style: { width: `${width}px` } }))),
          h('thead', [h('tr', headers.map((label, index) => h('th', [
            h('span', label),
            h('button', { class: 'column-resizer', type: 'button', title: '拖动调整列宽', onPointerdown: (event: PointerEvent) => startColumnResize(index, event) })
          ])))]),
          h('tbody', (props.rows as Dict[]).map(row => h('tr', [
            h('td', [renderTablePrimaryCell(props.library, row), h('small', row.description || row.body || row.comment_samples || row.signature || '')]),
            h('td', row.follow_status || row.competitor_status || row.status || '-'),
            h('td', row.task_name || row.task_id || '-'),
            h('td', row.profile_url || row.content_url ? h('a', { href: row.profile_url || row.content_url, target: '_blank' }, '打开链接') : '-'),
            h('td', { class: 'row-actions' }, [
              ['competitor_candidates', 'lead_customers'].includes(props.library) ? h('button', { class: 'icon-button', title: 'AI分析', onClick: () => emit('analyze-row', props.library, row) }, [h(MagicStick)]) : null,
              accountLibraries.includes(props.library) ? h('button', {
                class: ['icon-button', row.platform === 'ks' ? 'is-disabled' : ''],
                disabled: row.platform === 'ks',
                title: row.platform === 'ks' ? '快手主页资料暂不能从 SQLite 补回' : '补资料',
                onClick: () => emit('enrich-profile', props.library, row)
              }, [h(Refresh)]) : null,
              props.library === 'target_customers' && row.script ? h('button', { class: 'icon-button', title: '复制话术', onClick: () => navigator.clipboard.writeText(row.script) }, [h(CopyDocument)]) : null,
              h('button', { class: 'icon-button danger', title: '删除', onClick: () => emit('delete-row', props.library, row) }, [h(Delete)])
            ])
          ])))
        ])
        ])
      ])
    ])
  }
})

function renderTablePrimaryCell(library: string, row: Dict) {
  const label = row.nickname || row.title || row.commenter_nickname || row.body || row.id
  const href = tablePrimaryHref(library, row)
  if (!href) return h('strong', label)
  return h('a', { class: 'table-primary-link', href, target: '_blank', rel: 'noreferrer' }, label)
}

function tablePrimaryHref(library: string, row: Dict) {
  if (library === 'contents') return row.content_url || row.author_url || ''
  if (library === 'comments') return row.content_url || row.commenter_url || ''
  return row.profile_url || row.content_url || row.commenter_url || row.author_url || ''
}

const SettingsView = defineComponent({
  props: { settings: { type: Object, required: true }, env: { type: Object, required: true } },
  emits: ['save', 'check-env', 'clear-data'],
  setup(props, { emit }) {
    const local = reactive<Dict>({})
    function sync() {
      Object.keys(local).forEach(key => delete local[key])
      Object.assign(local, JSON.parse(JSON.stringify(props.settings || {})))
      local.icp_profile = normalizeIcpProfile(local.icp_profile)
    }
    watch(() => props.settings, sync, { immediate: true, deep: true })
    return () => {
      if (!local.icp_profile || typeof local.icp_profile !== 'object') {
        local.icp_profile = normalizeIcpProfile(local.icp_profile)
      }
      const icpProfile = local.icp_profile as Dict
      return h(SplitPane, { storageKey: 'settings', side: 'right', defaultSideWidth: 360 }, {
        default: () => [
        h('section', { class: 'pane primary-pane' }, [
          h('div', { class: 'section-title' }, [h('h2', '基础配置'), h('span', '没有配置时 AI 分析会明确失败')]),
          h('div', { class: 'form-grid' }, [
            inputField(local, 'media_crawler_path', 'MediaCrawler路径'),
            inputField(local, 'media_crawler_db_path', '底层SQLite路径'),
            inputField(local, 'ai_base_url', 'AI Base URL'),
            inputField(local, 'ai_api_key', 'API Key', 'password'),
            inputField(local, 'ai_model', '模型名'),
            inputField(local, 'default_content_count', '默认内容数', 'number'),
            inputField(local, 'default_comment_count', '默认评论数', 'number'),
            inputField(local, 'max_concurrency', '默认并发', 'number')
          ]),
          h('div', { class: 'toggles' }, [
            toggleField(local, 'headless', '默认无头模式'),
            toggleField(local, 'auto_analyze_competitors', '自动分析竞品账号'),
            toggleField(local, 'auto_analyze_leads', '自动分析线索用户')
          ]),
          h('div', { class: 'section-title compact' }, [h('h2', 'ICP画像'), h('span', 'AI筛选时会带入这些信息')]),
          h('div', { class: 'icp-grid' }, icpFields.map(field => renderIcpField(icpProfile, field))),
          h('div', { class: 'action-row' }, [
            h('button', {
              class: 'primary-action',
              onClick: () => emit('save', { ...local, icp_profile: buildIcpPayload(icpProfile) })
            }, [h(Check, { class: 'inline-icon' }), '保存设置'])
          ])
        ])
        ],
        side: () => [
        h('aside', { class: 'pane side-pane' }, [
          h('div', { class: 'section-title' }, [h('h2', '环境状态'), h('span', '运行前先检查')]),
          renderEnv(props.env),
          h('button', { class: 'wide-action', onClick: () => emit('check-env') }, [h(Refresh, { class: 'inline-icon' }), '重新检查']),
          h('div', { class: 'danger-zone' }, [
            h('div', { class: 'section-title compact' }, [h('h2', '危险操作'), h('span', '不可恢复')]),
            h('p', '清空项目库和 MediaCrawler 底层库中的所有采集、线索、AI、日志数据。'),
            h('button', { class: 'wide-action danger-action', onClick: () => emit('clear-data') }, [h(Delete, { class: 'inline-icon' }), '清空所有数据'])
          ])
        ])
        ]
      })
    }
  }
})

function renderIcpField(profile: Dict, field: Dict) {
  const control = field.multiline
    ? h('textarea', {
        value: profile[field.key] || '',
        placeholder: field.placeholder,
        onInput: (event: Event) => profile[field.key] = (event.target as HTMLTextAreaElement).value
      })
    : h('input', {
        value: profile[field.key] || '',
        placeholder: field.placeholder,
        onInput: (event: Event) => profile[field.key] = (event.target as HTMLInputElement).value
      })
  return h('label', { class: ['icp-field', field.multiline ? 'icp-field-wide' : ''] }, [
    h('span', field.label),
    control
  ])
}

function inputField(local: Dict, key: string, label: string, type = 'text') {
  return h('label', [label, h('input', { type, value: local[key] || '', onInput: (event: Event) => local[key] = (event.target as HTMLInputElement).value })])
}

function toggleField(local: Dict, key: string, label: string) {
  return h('label', [h('input', { type: 'checkbox', checked: Boolean(local[key]), onChange: (event: Event) => local[key] = (event.target as HTMLInputElement).checked }), label])
}

function renderEnv(envValue: Dict) {
  const items = [
    ['项目库', envValue?.project_db],
    ['MediaCrawler路径', envValue?.media_crawler_path],
    ['底层SQLite', envValue?.media_crawler_db],
    ['AI配置', envValue?.ai_config]
  ]
  return h('div', { class: 'env-stack' }, [
    h('div', { class: 'env-list' }, items.map(([label, item]: any) => h('div', { class: 'env-item' }, [
      h('span', label),
      h('strong', { class: item?.ok ? 'ok' : 'warn' }, item?.ok ? '正常' : '待处理'),
      h('small', item?.path || item?.base_url || item?.model || '')
    ]))),
    renderPlatformDiagnostics(envValue?.platform_diagnostics || [])
  ])
}

function renderPlatformDiagnostics(platforms: Dict[]) {
  if (!platforms.length) {
    return h('div', { class: 'diagnostic-empty' }, '底层 SQLite 不存在或尚未完成检查')
  }
  return h('div', { class: 'platform-diagnostics' }, [
    h('div', { class: 'section-title compact' }, [h('h2', '平台数据诊断'), h('span', '原始表与关键字段')]),
    ...platforms.map(platform => h('div', { class: 'diagnostic-panel' }, [
      h('div', { class: 'diagnostic-head' }, [
        h('strong', platform.label || platform.platform),
        h('span', { class: platform.ok ? 'ok' : 'warn' }, platform.ok ? '可导入' : '缺内容表')
      ]),
      h('div', { class: 'diagnostic-tables' }, [
        renderRawTableMetric('内容', platform.tables?.content),
        renderRawTableMetric('评论', platform.tables?.comment),
        renderRawTableMetric('主页', platform.tables?.creator)
      ]),
      h('div', { class: 'field-quality' }, importantDiagnosticFields(platform).map(field => h('span', {
        class: field.supported && field.row_count && field.non_empty === 0 ? 'field-warn' : ''
      }, `${field.label} ${field.supported ? `${field.non_empty}/${field.row_count}` : '不支持'}`))),
      platform.warnings?.length ? h('ul', { class: 'diagnostic-warnings' }, platform.warnings.map((warning: string) => h('li', warning))) : null
    ]))
  ])
}

function renderRawTableMetric(label: string, table: Dict = {}) {
  return h('div', [
    h('span', label),
    h('strong', table.exists ? `${table.row_count || 0}` : '缺失'),
    h('small', table.table || '无映射')
  ])
}

function importantDiagnosticFields(platform: Dict) {
  const contentFields = platform.tables?.content?.fields || []
  const commentFields = platform.tables?.comment?.fields || []
  const creatorFields = platform.tables?.creator?.fields || []
  const pick = (fields: Dict[], key: string) => fields.find(field => field.key === key)
  return [
    pick(contentFields, 'author_id'),
    pick(contentFields, 'nickname'),
    pick(contentFields, 'signature'),
    pick(commentFields, 'body'),
    pick(creatorFields, 'signature'),
    pick(creatorFields, 'fans')
  ].filter((field): field is Dict => Boolean(field))
}
</script>

<style>
.shell {
  min-height: 100vh;
  background: linear-gradient(180deg, #f7faf8 0%, #eef4f2 100%);
}

.sidebar {
  display: flex;
  flex-direction: column;
  border-right: 1px solid #dbe7e2;
  background: #ffffff;
}

.brand {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 22px 18px;
  border-bottom: 1px solid #e5ece8;
}

.brand-mark {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border-radius: 8px;
  color: #ffffff;
  background: #0f766e;
  font-weight: 800;
}

.brand strong,
.brand span {
  display: block;
}

.brand span,
.sidebar-status small,
.workflow-step small,
.section-title span,
.task-row span,
.data-table small,
.env-item small {
  color: #64748b;
  overflow-wrap: anywhere;
}

.nav {
  flex: 1;
  border-right: 0;
}

.sidebar-status {
  margin: 16px;
  padding: 14px;
  border: 1px solid #dbe7e2;
  border-radius: 8px;
  background: #f8fbfa;
}

.sidebar-status strong {
  display: block;
  margin-top: 6px;
  color: #0f766e;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 78px;
  padding: 0 28px;
  border-bottom: 1px solid #dbe7e2;
  background: rgba(255, 255, 255, 0.9);
}

.topbar h1 {
  margin: 0;
  font-size: 22px;
}

.topbar p {
  margin: 5px 0 0;
  color: #64748b;
}

.topbar-actions,
.action-row,
.button-pair,
.row-actions,
.toggles {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.main {
  height: calc(100vh - 78px);
  overflow: auto;
  padding: 22px;
}

.workflow-strip {
  display: grid;
  grid-template-columns: repeat(5, minmax(150px, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}

.workflow-step {
  display: flex;
  gap: 10px;
  min-height: 68px;
  padding: 12px;
  border: 1px solid #dbe7e2;
  border-radius: 8px;
  background: #ffffff;
}

.workflow-step.active {
  border-color: #0f766e;
  box-shadow: 0 0 0 2px rgba(15, 118, 110, 0.1);
}

.workflow-step span {
  color: #0f766e;
  font-weight: 800;
}

.workflow-step strong,
.workflow-step small {
  display: block;
}

.split-pane {
  display: grid;
  gap: 0;
  align-items: start;
}

.split-resizer {
  width: 10px;
  min-height: 520px;
  border: 0;
  cursor: col-resize;
  background: transparent;
  position: relative;
}

.split-resizer::before {
  content: '';
  position: absolute;
  top: 12px;
  bottom: 12px;
  left: 4px;
  width: 2px;
  border-radius: 999px;
  background: #cbd5e1;
}

.split-resizer:hover::before,
.split-resizer:focus-visible::before,
.split-pane.dragging .split-resizer::before {
  background: #0f766e;
}

.split-resizer:focus-visible {
  outline: 2px solid rgba(15, 118, 110, 0.25);
  outline-offset: 2px;
}

.pane {
  min-height: 520px;
  border: 1px solid #dbe7e2;
  border-radius: 8px;
  background: #ffffff;
  padding: 18px;
  overflow: auto;
}

.side-pane {
  max-height: calc(100vh - 188px);
}

.log-pane {
  overflow: hidden;
}

.section-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.section-title.compact {
  margin-top: 18px;
}

.section-title h2 {
  margin: 0;
  font-size: 17px;
}

.mode-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.mode-option,
.task-row,
.library-list button,
.wide-action,
.primary-action,
.icon-button {
  cursor: pointer;
}

.mode-option {
  min-height: 116px;
  padding: 14px;
  border: 1px solid #dbe7e2;
  border-radius: 8px;
  background: #f9fbfa;
  text-align: left;
}

.mode-option.selected {
  border-color: #0f766e;
  background: #ecfdf5;
}

.mode-option small,
.mode-option strong,
.mode-option span {
  display: block;
}

.mode-option small {
  color: #b45309;
  font-weight: 700;
}

.mode-option strong {
  margin: 8px 0;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(160px, 1fr));
  gap: 12px;
  margin: 18px 0;
}

label {
  display: grid;
  gap: 6px;
  color: #475569;
  font-size: 13px;
}

.form-field.field-wide {
  grid-column: span 2;
}

.form-field.field-full {
  grid-column: 1 / -1;
}

input,
select,
textarea {
  width: 100%;
  min-height: 36px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 8px 10px;
  background: #ffffff;
  color: #1f2937;
}

input:disabled {
  background: #f1f5f9;
  color: #94a3b8;
  cursor: not-allowed;
}

.tag-input {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 44px;
  padding: 6px 8px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #ffffff;
  cursor: text;
}

.tag-input:focus-within {
  border-color: #2563eb;
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.12);
}

.tag-input.disabled {
  background: #f1f5f9;
  cursor: not-allowed;
}

.tag-input input {
  flex: 1 1 180px;
  min-width: 140px;
  min-height: 30px;
  border: 0;
  padding: 4px 6px;
  background: transparent;
  outline: 0;
}

.tag-input input:disabled {
  background: transparent;
}

.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  min-height: 30px;
  padding: 0 8px 0 10px;
  border: 1px solid #bfdbfe;
  border-radius: 6px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 13px;
}

.tag-chip-text {
  overflow-wrap: anywhere;
}

.tag-remove {
  display: inline-grid;
  place-items: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  color: #2563eb;
  background: transparent;
  cursor: pointer;
}

.tag-remove:hover,
.tag-remove:focus-visible {
  background: #dbeafe;
}

.tag-remove-icon {
  width: 14px;
  height: 14px;
}

input[type='checkbox'] {
  width: 18px;
  height: 18px;
  min-height: 18px;
  padding: 0;
  accent-color: #0f766e;
}

.toggles label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #334155;
}

.primary-action,
.wide-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 38px;
  padding: 0 14px;
  border: 0;
  border-radius: 6px;
  color: #ffffff;
  background: #0f766e;
}

.wide-action {
  width: 100%;
  margin-bottom: 10px;
}

.danger-action {
  background: #dc2626;
}

.inline-icon,
.icon-button svg {
  width: 16px;
  height: 16px;
}

.task-list,
.tree-list,
.env-list {
  display: grid;
  gap: 10px;
}

.task-row {
  display: grid;
  gap: 5px;
  width: 100%;
  padding: 12px;
  border: 1px solid #dbe7e2;
  border-radius: 8px;
  background: #ffffff;
  text-align: left;
}

.status {
  display: inline-flex;
  width: fit-content;
  padding: 2px 8px;
  border-radius: 999px;
  background: #e2e8f0;
  color: #334155;
  font-style: normal;
  font-size: 12px;
}

.status.succeeded {
  background: #dcfce7;
  color: #166534;
}

.status.failed {
  background: #fee2e2;
  color: #991b1b;
}

.status.running {
  background: #dbeafe;
  color: #1d4ed8;
}

.metric-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.metric-tile {
  padding: 14px;
  border-radius: 8px;
  border: 1px solid #dbe7e2;
}

.metric-tile small,
.metric-tile strong {
  display: block;
}

.metric-tile strong {
  margin-top: 8px;
  font-size: 24px;
}

.accent-green {
  background: #ecfdf5;
}

.accent-amber {
  background: #fffbeb;
}

.accent-blue {
  background: #eff6ff;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
}

.table-scroll {
  overflow: auto;
}

.data-table.resizable-table {
  table-layout: fixed;
}

.data-table th,
.data-table td {
  padding: 12px;
  border-bottom: 1px solid #e5ece8;
  text-align: left;
  vertical-align: top;
}

.data-table th {
  position: relative;
  background: #f8fafc;
  color: #475569;
  font-size: 13px;
}

.data-table th span {
  display: block;
  padding-right: 10px;
}

.column-resizer {
  position: absolute;
  top: 0;
  right: -4px;
  width: 8px;
  height: 100%;
  border: 0;
  background: transparent;
  cursor: col-resize;
  z-index: 1;
}

.column-resizer::after {
  content: '';
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 3px;
  width: 2px;
  border-radius: 999px;
  background: transparent;
}

.column-resizer:hover::after,
.column-resizer:focus-visible::after {
  background: #0f766e;
}

.data-table td strong,
.data-table td small {
  display: block;
}

.data-table td {
  overflow-wrap: anywhere;
}

.table-primary-link {
  display: block;
  color: #0f4fb3;
  font-weight: 700;
  text-decoration: none;
  overflow-wrap: anywhere;
}

.table-primary-link:hover {
  text-decoration: underline;
}

.icon-button {
  display: inline-grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border: 1px solid #dbe7e2;
  border-radius: 6px;
  background: #ffffff;
}

.icon-button:disabled,
.icon-button.is-disabled {
  cursor: not-allowed;
  color: #9aa8a0;
  background: #f3f6f4;
  border-color: #e4ebe7;
}

.danger,
.icon-button.danger {
  color: #b91c1c;
}

.table-workspace {
  min-height: calc(100vh - 188px);
}

.table-library-bar {
  display: grid;
  gap: 12px;
  margin-bottom: 18px;
  padding-bottom: 16px;
  border-bottom: 1px solid #e5ece8;
}

.library-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.library-list button {
  min-height: 36px;
  border: 1px solid #dbe7e2;
  border-radius: 999px;
  background: #ffffff;
  padding: 0 14px;
  color: #334155;
  white-space: nowrap;
}

.library-list button.selected {
  border-color: #0f766e;
  color: #0f766e;
  background: #ecfdf5;
}

.overview-pane {
  min-height: calc(100vh - 188px);
}

.overview-table {
  display: grid;
  gap: 10px;
  min-width: 920px;
}

.overview-row {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(420px, auto);
  gap: 14px;
  align-items: center;
  min-height: 50px;
  padding: 8px 14px;
  border: 1px solid #dbe7e2;
  border-radius: 8px;
  background: #ffffff;
}

.overview-row.platform {
  border-color: transparent;
  background: transparent;
  padding: 4px 0;
}

.overview-row.keyword {
  border-color: #bfdbfe;
  background: #f8fbff;
}

.overview-row.account {
  background: #ffffff;
}

.overview-main {
  display: flex;
  gap: 14px;
  align-items: center;
  min-width: 0;
}

.overview-title {
  min-width: 0;
}

.overview-title strong,
.overview-link,
.overview-title small {
  display: block;
}

.overview-title strong,
.overview-link {
  color: #0f4fb3;
  font-size: 14px;
  font-weight: 700;
  text-decoration: none;
  overflow-wrap: anywhere;
}

.overview-link:hover {
  text-decoration: underline;
}

.overview-row.platform .overview-title strong {
  color: #0f172a;
  font-size: 16px;
}

.overview-title small {
  margin-top: 3px;
  color: #64748b;
  font-size: 12px;
  overflow-wrap: anywhere;
}

.overview-metrics {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}

.metric-chip {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 7px;
  background: #f1f5f9;
  color: #334155;
  font-size: 12px;
  white-space: nowrap;
}

.outline-button {
  min-width: 56px;
  min-height: 30px;
  border: 1px solid #bfdbfe;
  border-radius: 7px;
  background: #eff6ff;
  color: #2563eb;
  font-size: 12px;
  cursor: pointer;
}

.overview-spacer {
  width: 56px;
  flex: 0 0 56px;
}

.empty-state {
  display: grid;
  min-height: 260px;
  place-items: center;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  color: #64748b;
}

.json-view {
  min-height: 430px;
  border: 1px solid #e5ece8;
  border-radius: 8px;
  background: #0f172a;
  color: #d1fae5;
  padding: 14px;
  overflow: auto;
}

.log-console {
  height: clamp(320px, calc(100vh - 260px), 560px);
  border: 1px solid #e5ece8;
  border-radius: 8px;
  background: #0f172a;
  color: #d1fae5;
  padding: 14px;
  overflow: auto;
}

.log-console p {
  display: grid;
  grid-template-columns: 160px minmax(0, 1fr);
  gap: 12px;
  margin: 0 0 8px;
  font-family: Consolas, monospace;
  font-size: 12px;
}

.log-console time {
  color: #93c5fd;
}

.button-pair {
  margin-top: 14px;
}

.button-pair button {
  flex: 1;
  min-height: 36px;
  border: 1px solid #dbe7e2;
  border-radius: 6px;
  background: #ffffff;
}

.icp-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(220px, 1fr));
  gap: 12px;
}

.icp-field span {
  color: #334155;
  font-weight: 700;
}

.icp-field textarea {
  min-height: 86px;
  resize: vertical;
  line-height: 1.55;
}

.icp-field-wide {
  grid-column: span 2;
}

.env-item {
  display: grid;
  min-width: 0;
  gap: 4px;
  padding: 12px;
  border: 1px solid #dbe7e2;
  border-radius: 8px;
}

.env-item .ok {
  color: #166534;
}

.env-item .warn {
  color: #b45309;
}

.env-stack,
.platform-diagnostics,
.diagnostic-panel {
  display: grid;
  gap: 10px;
  min-width: 0;
  width: 100%;
}

.diagnostic-panel {
  box-sizing: border-box;
  padding: 12px;
  border: 1px solid #dbe7e2;
  border-radius: 8px;
  background: #ffffff;
}

.diagnostic-head,
.diagnostic-tables,
.field-quality {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.diagnostic-head {
  justify-content: space-between;
}

.diagnostic-head strong {
  color: #0f3d3a;
}

.diagnostic-tables > div {
  display: grid;
  min-width: 78px;
  gap: 2px;
  padding: 7px 8px;
  border-radius: 6px;
  background: #f4f8f6;
}

.diagnostic-tables span,
.field-quality span {
  font-size: 12px;
  color: #64748b;
}

.diagnostic-tables strong {
  color: #1f2937;
  font-size: 14px;
}

.diagnostic-tables small {
  color: #8a9a93;
  font-size: 11px;
  overflow-wrap: anywhere;
}

.field-quality span {
  padding: 4px 7px;
  border-radius: 999px;
  background: #eef6f3;
}

.field-quality .field-warn {
  color: #9a3412;
  background: #fff7ed;
}

.diagnostic-warnings {
  margin: 0;
  padding-left: 18px;
  color: #92400e;
  font-size: 12px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.diagnostic-empty {
  padding: 12px;
  border: 1px dashed #cbd5d1;
  border-radius: 8px;
  color: #64748b;
  font-size: 13px;
}

.danger-zone {
  margin-top: 18px;
  padding: 14px;
  border: 1px solid #fecaca;
  border-radius: 8px;
  background: #fff7f7;
}

.danger-zone p {
  margin: 0 0 12px;
  color: #7f1d1d;
  font-size: 13px;
  line-height: 1.6;
}

@media (max-width: 1100px) {
  /* 窄屏按文档流纵向展开，避免固定高度容器把内容挤出视口。 */
  .shell,
  .shell > .el-container {
    display: block;
    min-height: 100vh;
  }

  .workflow-strip,
  .split-pane,
  .mode-grid,
  .form-grid,
  .icp-grid {
    grid-template-columns: 1fr;
  }

  .form-field.field-wide,
  .form-field.field-full,
  .icp-field-wide {
    grid-column: auto;
  }

  .sidebar {
    display: none;
  }

  .topbar {
    height: auto;
    min-height: 78px;
    align-items: flex-start;
    gap: 14px;
    padding: 16px 22px;
    flex-wrap: wrap;
  }

  .topbar-actions {
    justify-content: flex-start;
  }

  .main {
    height: auto;
    min-height: calc(100vh - 78px);
    overflow: visible;
  }

  .split-pane {
    row-gap: 16px;
  }

  .split-resizer {
    display: none;
  }

  .pane,
  .side-pane {
    min-height: auto;
    max-height: none;
  }

  .pane {
    overflow-x: auto;
  }

  .data-table {
    min-width: 720px;
  }
}

@media (max-width: 640px) {
  .topbar h1 {
    font-size: 20px;
  }

  .topbar p {
    font-size: 13px;
  }

  .main {
    padding: 16px;
  }

  .section-title {
    align-items: flex-start;
    flex-direction: column;
  }

  .workflow-step,
  .pane {
    padding: 14px;
  }
}
</style>
