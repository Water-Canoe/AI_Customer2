<template>
  <el-container class="shell">
    <el-aside class="sidebar" width="236px">
      <div class="brand">
        <div class="brand-mark">AI</div>
        <div>
          <strong>AI获客系统</strong>
          <span>拓客 · 引流 · 跟进</span>
        </div>
      </div>
      <el-menu :default-active="activeView" :default-openeds="['lead-workbench', 'traffic-workbench']" class="nav" @select="goToView">
        <el-sub-menu index="lead-workbench">
          <template #title><el-icon><Operation /></el-icon><span>拓客工作台</span></template>
          <el-menu-item index="tasks"><el-icon><Operation /></el-icon><span>任务管理</span></el-menu-item>
          <el-menu-item index="logs"><el-icon><Tickets /></el-icon><span>任务与日志</span></el-menu-item>
          <el-menu-item index="overview"><el-icon><Share /></el-icon><span>总览树</span></el-menu-item>
          <el-menu-item index="ai"><el-icon><MagicStick /></el-icon><span>AI分析</span></el-menu-item>
          <el-menu-item index="message-workbench"><el-icon><Message /></el-icon><span>私信工作台</span></el-menu-item>
          <el-menu-item index="tables"><el-icon><Grid /></el-icon><span>数据表</span></el-menu-item>
          <el-menu-item index="settings"><el-icon><Setting /></el-icon><span>设置</span></el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="traffic-workbench">
          <template #title><el-icon><Promotion /></el-icon><span>引流工作台</span></template>
          <el-menu-item index="traffic-plans"><el-icon><Promotion /></el-icon><span>计划工作台</span></el-menu-item>
          <el-menu-item index="traffic-monitor"><el-icon><Tickets /></el-icon><span>执行监控</span></el-menu-item>
          <el-menu-item index="traffic-records"><el-icon><Grid /></el-icon><span>操作记录</span></el-menu-item>
          <el-menu-item index="traffic-settings"><el-icon><Setting /></el-icon><span>引流设置</span></el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="topbar">
        <div class="topbar-heading">
          <span class="topbar-kicker">{{ topbarKicker }}</span>
          <h1>{{ viewTitle }}</h1>
          <p>{{ viewSubtitle }}</p>
        </div>
        <div class="topbar-insights" aria-label="当前工作台指标">
          <div
            v-for="item in dashboardInsights"
            :key="item.label"
            class="topbar-insight"
            :class="`tone-${item.tone}`"
          >
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
        </div>
        <div class="topbar-actions">
          <span class="topbar-env-tag" :class="topbarEnvOk ? 'is-ok' : 'is-warn'">{{ topbarEnvLabel }}</span>
          <el-button :icon="Refresh" @click="refreshAll">刷新</el-button>
          <el-button type="primary" :icon="Plus" @click="createFromTopbar">{{ topbarPrimaryAction }}</el-button>
        </div>
      </el-header>

      <el-main class="main" :class="`view-${activeView}`">
        <RouterView v-slot="{ Component }">
          <component :is="Component" v-bind="routeProps" v-on="routeListeners" />
        </RouterView>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import {
  Grid,
  MagicStick,
  Message,
  Operation,
  Plus,
  Promotion,
  Refresh,
  Setting,
  Share,
  Tickets,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { api } from './shared/api'
import type { Dict } from './shared/types'
import { competitorStatusLabel, platformName } from './shared/format'

const router = useRouter()
const route = useRoute()

const activeLibrary = ref('contents')
const tableStatus = ref('')
const tableKeyword = ref('')
const tasks = ref<Dict[]>([])
const tableRows = ref<Dict[]>([])
const tableLoading = ref(false)
const overviewTree = ref<Dict[]>([])
const aiJobs = ref<Dict[]>([])
const aiWorkbench = ref<Dict>({})
const selectedTask = ref<Dict | null>(null)
const taskDiagnostics = ref<Dict>({})
const taskDedupSummary = ref<Dict>({})
const retryDraft = ref<Dict | null>(null)
const leadRows = ref<Dict[]>([])
const competitorRows = ref<Dict[]>([])
const settings = ref<Dict>({})
const settingsDraftDirty = ref(false)
const settingsSaving = ref(false)
const settingsSaveRevision = ref(0)
const env = ref<Dict>({})
const messageKeywords = ref<Dict[]>([])
const messageCustomers = ref<Dict>({ rows: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
const messageDetail = ref<Dict>({})
const messageLoading = ref(false)
const messageFilters = ref<Dict>({ keyword: '', status: '待私信', query: '', page: 1, page_size: 20 })
const messageBatches = ref<Dict>({ batches: [], active: null, items: [] })
const trafficRuns = ref<Dict[]>([])
const trafficEnv = ref<Dict>({})
const trafficRefreshSeq = ref(0)
const tombstoneSummary = ref<Dict>({})
const tombstones = ref<Dict>({ items: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
const tombstoneFilters = ref<Dict>({ entity_type: '', platform: '', source: '', query: '', page: 1, page_size: 20 })
const autoSyncing = ref(false)
const lastAutoSyncAt = ref(0)

const AUTO_SYNC_ACTIVE_MS = 3000
const AUTO_SYNC_IDLE_MS = 12000
let autoSyncTimer: ReturnType<typeof window.setInterval> | null = null
let settingsMutationSeq = 0

const activeView = computed(() => String(route.name || 'tasks'))
const isTrafficView = computed(() => activeView.value.startsWith('traffic-'))
const topbarKicker = computed(() => isTrafficView.value ? '引流工作台' : '拓客工作台')
const viewTitle = computed(() => String(route.meta.title || '任务管理'))
const viewSubtitle = computed(() => String(route.meta.subtitle || ''))
const envReady = computed(() => Boolean(env.value?.media_crawler_path?.ok && env.value?.media_crawler_db?.ok))
const topbarEnvOk = computed(() => isTrafficView.value ? Boolean(trafficEnv.value?.ok) : envReady.value)
const topbarEnvLabel = computed(() => {
  if (isTrafficView.value) return topbarEnvOk.value ? '引流环境正常' : '需要检查引流环境'
  return topbarEnvOk.value ? '环境就绪' : '需要检查环境'
})
const topbarPrimaryAction = computed(() => {
  return isTrafficView.value ? '新建计划' : '新建任务'
})
const hasActiveAsyncWork = computed(() => {
  return tasks.value.some(task => isActiveStatus(task.status))
    || aiJobs.value.some(job => isActiveStatus(job.status))
    || isActiveStatus(messageBatches.value?.active?.status)
    || trafficRuns.value.some(run => isActiveStatus(run.status))
})
const dashboardInsights = computed(() => {
  if (isTrafficView.value) {
    const activeRunCount = trafficRuns.value.filter(run => isActiveStatus(run.status)).length
    const successCount = trafficRuns.value.reduce((total, run) => total + Number(run.action_success_count || 0), 0)
    const failedCount = trafficRuns.value.reduce((total, run) => total + Number(run.failed_count || 0) + Number(run.skipped_count || 0), 0)
    return [
      { label: '运行批次', value: compactCount(activeRunCount), tone: 'blue' },
      { label: '成功动作', value: compactCount(successCount), tone: 'green' },
      { label: '失败/跳过', value: compactCount(failedCount), tone: 'red' },
    ]
  }
  const summary = aiWorkbench.value?.summary || {}
  const pendingAi = Number(summary.competitor_pending || 0) + Number(summary.lead_pending || 0)
  const failedAi = Number(summary.failed || 0)
  const activeTaskCount = tasks.value.filter(task => isActiveStatus(task.status)).length
  const failedTaskCount = tasks.value.filter(task => String(task.status || '') === 'failed').length
  return [
    { label: '运行任务', value: compactCount(activeTaskCount), tone: 'blue' },
    { label: 'AI待处理', value: compactCount(pendingAi), tone: 'amber' },
    { label: '待私信', value: compactCount(messageCustomers.value?.total || 0), tone: 'green' },
    { label: '失败待查', value: compactCount(failedTaskCount + failedAi), tone: 'red' },
  ]
})

const routeProps = computed(() => {
  if (activeView.value === 'tasks') {
    return {
      tasks: tasks.value,
      settings: settings.value,
      retryDraft: retryDraft.value || undefined,
    }
  }
  if (activeView.value === 'overview') return { tree: overviewTree.value }
  if (activeView.value === 'ai') return { workbench: aiWorkbench.value, jobs: aiJobs.value, leadRows: leadRows.value, competitorRows: competitorRows.value }
  if (activeView.value === 'message-workbench') {
    return {
      keywords: messageKeywords.value,
      customers: messageCustomers.value,
      detail: messageDetail.value,
      filters: messageFilters.value,
      loading: messageLoading.value,
      batches: messageBatches.value,
      settings: settings.value,
    }
  }
  if (activeView.value === 'logs') {
    return {
      tasks: tasks.value,
      selectedTask: selectedTask.value || undefined,
      diagnostics: taskDiagnostics.value,
      dedupSummary: taskDedupSummary.value,
    }
  }
  if (activeView.value === 'tables') {
    return {
      library: activeLibrary.value,
      rows: tableRows.value,
      loading: tableLoading.value,
      statusFilter: tableStatus.value,
      keywordFilter: tableKeyword.value,
    }
  }
  if (activeView.value.startsWith('traffic-')) return { refreshSeq: trafficRefreshSeq.value }
  return {
    settings: settings.value,
    settingsSaveRevision: settingsSaveRevision.value,
    env: env.value,
    tombstoneSummary: tombstoneSummary.value,
    tombstones: tombstones.value,
    tombstoneFilters: tombstoneFilters.value,
  }
})

const routeListeners = computed(() => {
  // 只把当前页面声明的事件传下去，避免 fragment 页面收到无关监听器 warning。
  if (activeView.value === 'tasks') {
    return {
      'create-task': createTask,
      'open-logs': openTaskLogs,
      'consume-retry-draft': consumeRetryDraft,
    }
  }
  if (activeView.value === 'overview') {
    return {
      'account-analyze': analyzeOverviewAccount,
      'keyword-analyze': analyzeKeywordCompetitors,
      'customer-intent-analyze': analyzeOverviewCustomerIntent,
      'account-customers-analyze': analyzeAccountCustomersIntent,
      'customer-message': messageOverviewCustomer,
      'customer-follow-update': updateOverviewCustomerFollowStatus,
      'delete-account': deleteOverviewAccount,
      'delete-customer': deleteOverviewCustomer,
      'delete-account-noncustomers': deleteAccountNonCustomers,
      'delete-platform': deleteOverviewPlatform,
      'delete-keyword': deleteOverviewKeyword,
      'delete-keyword-noncompetitors': deleteKeywordNonCompetitors,
      'find-customers': findCustomers,
    }
  }
  if (activeView.value === 'ai') {
    return {
      'create-job': createAiJob,
      'create-batch-jobs': createBatchAiJobs,
      'delete-non-competitors': deleteAiWorkbenchNonCompetitors,
      'delete-non-customers': deleteAiWorkbenchNonCustomers,
      'retry-job': retryAiJob,
      'retry-jobs': retryAiJobs,
    }
  }
  if (activeView.value === 'message-workbench') {
    return {
      'filter-change': changeMessageWorkbenchFilter,
      'select-customer': selectMessageWorkbenchCustomer,
      'message-customer': messageWorkbenchCustomer,
      'auto-message-customer': autoMessageWorkbenchCustomer,
      'start-auto-message-batch': startMessageAutoBatch,
      'cancel-auto-message-batch': cancelMessageAutoBatch,
      'retry-auto-message-batch': retryMessageAutoBatch,
      'delete-auto-message-batch': deleteMessageAutoBatch,
      'update-follow-status': updateMessageWorkbenchFollowStatus,
      'close-detail': closeMessageWorkbenchDetail,
    }
  }
  if (activeView.value === 'logs') {
    return {
      'select-task': selectTask,
      'retry-task': retryTask,
      'cancel-task': cancelTask,
      'archive-task': archiveTask,
      'delete-task': deleteTask,
    }
  }
  if (activeView.value === 'tables') {
    return {
      'change-library': changeLibrary,
      'change-filter': changeTableFilter,
      'update-row': updateRow,
      'delete-row': deleteRow,
      'analyze-row': analyzeTableRow,
      'enrich-profile': enrichProfile,
      'find-customers': findCustomers,
    }
  }
  if (activeView.value.startsWith('traffic-')) return {}
  return {
    save: saveSettings,
    'settings-dirty-change': (dirty: boolean) => settingsDraftDirty.value = dirty,
    'check-env': checkEnv,
    'load-tombstones': loadTombstones,
    'clear-data': clearAllData,
  }
})

function goToView(view: string) {
  router.push(`/${view}`)
}

async function refreshAll() {
  if (isTrafficView.value) {
    await loadTrafficShell()
    trafficRefreshSeq.value += 1
    lastAutoSyncAt.value = Date.now()
    return
  }
  // 首页各面板独立加载，单个接口失败时不阻塞其它工作区。
  await Promise.allSettled([loadTasks(), loadSettings(), checkEnv(), loadAiJobs(), loadOverview(), loadMessageWorkbench(true), loadTombstoneSummary(), loadTombstones(), loadTable(activeLibrary.value)])
  lastAutoSyncAt.value = Date.now()
}

function createFromTopbar() {
  router.push(isTrafficView.value ? '/traffic-plans' : '/tasks')
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
  const requestSeq = settingsMutationSeq
  const { data } = await api.get('/settings')
  // 保存中或已有本地草稿时，忽略可能较旧的设置响应。
  if (requestSeq !== settingsMutationSeq) return
  if (activeView.value === 'settings' && (settingsDraftDirty.value || settingsSaving.value)) return
  settings.value = data
}

async function checkEnv() {
  const { data } = await api.get('/settings/env-check')
  env.value = data
}

async function loadAiJobs() {
  const { data } = await api.get('/ai/jobs')
  aiJobs.value = data
  const workbench = await api.get('/ai/workbench')
  aiWorkbench.value = workbench.data
}

async function loadOverview() {
  const { data } = await api.get('/overview/tree')
  overviewTree.value = data
}

async function loadSelectedTaskDiagnostics(id?: string) {
  const taskId = id || selectedTask.value?.id
  if (!taskId) {
    taskDiagnostics.value = {}
    taskDedupSummary.value = {}
    return
  }
  const [diagnosticResult, dedupResult] = await Promise.allSettled([
    api.get(`/tasks/${taskId}/diagnostics`),
    api.get(`/tasks/${taskId}/dedup-summary`),
  ])
  taskDiagnostics.value = diagnosticResult.status === 'fulfilled' ? diagnosticResult.value.data : {}
  taskDedupSummary.value = dedupResult.status === 'fulfilled' ? dedupResult.value.data : {}
}

async function loadTombstoneSummary() {
  const { data } = await api.get('/tombstones/summary')
  tombstoneSummary.value = data
}

async function loadTombstones(filters: Dict = {}) {
  tombstoneFilters.value = { ...tombstoneFilters.value, ...filters }
  const { data } = await api.get('/tombstones', { params: tombstoneFilters.value })
  tombstones.value = data
}

async function loadMessageWorkbench(silent = false) {
  if (!silent) messageLoading.value = true
  try {
    const [keywords, customers, batches] = await Promise.all([
      api.get('/message-workbench/keywords'),
      api.get('/message-workbench/customers', { params: messageFilters.value }),
      api.get('/message-workbench/auto-message-batches')
    ])
    messageKeywords.value = keywords.data
    messageCustomers.value = customers.data
    messageBatches.value = batches.data
    const detailLeadId = messageDetail.value?.customer?.lead_id
    if (detailLeadId) {
      try {
        const detail = await api.get(`/message-workbench/customers/${detailLeadId}`)
        messageDetail.value = detail.data
      } catch (error: any) {
        if (error?.response?.status === 404) messageDetail.value = {}
        else throw error
      }
    }
  } finally {
    if (!silent) messageLoading.value = false
  }
}

async function loadTrafficShell() {
  // 引流页顶部栏只取轻量运行态，列表详情仍由当前子页面自己加载。
  const [runsResult, envResult] = await Promise.allSettled([
    api.get('/traffic/runs'),
    api.get('/traffic/environment-check'),
  ])
  if (runsResult.status === 'fulfilled') trafficRuns.value = runsResult.value.data
  if (envResult.status === 'fulfilled') trafficEnv.value = envResult.value.data
}

async function loadTable(library: string, silent = false) {
  if (!silent) tableLoading.value = true
  try {
    const { data } = await api.get(`/tables/${library}`, { params: { status: tableStatus.value, keyword: tableKeyword.value } })
    tableRows.value = data.rows
    if (library === 'lead_customers') leadRows.value = data.rows
    if (library === 'competitor_candidates') competitorRows.value = data.rows
  } finally {
    if (!silent) tableLoading.value = false
  }
}

async function refreshSelectedTask() {
  const taskId = selectedTask.value?.id
  if (!taskId) return
  try {
    selectedTask.value = await fetchTask(String(taskId))
    await loadSelectedTaskDiagnostics(String(taskId))
  } catch (error: any) {
    if (error?.response?.status === 404) {
      selectedTask.value = null
      taskDiagnostics.value = {}
      taskDedupSummary.value = {}
    }
    else throw error
  }
}

function isActiveStatus(status: unknown) {
  return ['pending', 'running'].includes(String(status || ''))
}

function compactCount(value: unknown) {
  const count = Number(value || 0)
  if (!Number.isFinite(count)) return '0'
  if (count >= 10000) return `${Math.round(count / 1000) / 10}万`
  if (count >= 1000) return `${Math.round(count / 100) / 10}k`
  return String(count)
}

function startAutoSync() {
  if (autoSyncTimer) window.clearInterval(autoSyncTimer)
  autoSyncTimer = window.setInterval(() => {
    void syncCurrentView('auto')
  }, 1000)
}

function stopAutoSync() {
  if (!autoSyncTimer) return
  window.clearInterval(autoSyncTimer)
  autoSyncTimer = null
}

function handleVisibilityChange() {
  if (!document.hidden) void syncCurrentView('visible')
}

async function syncCurrentView(reason: 'auto' | 'route' | 'visible') {
  if (autoSyncing.value) return
  if (document.hidden) return
  const interval = hasActiveAsyncWork.value ? AUTO_SYNC_ACTIVE_MS : AUTO_SYNC_IDLE_MS
  if (reason === 'auto' && Date.now() - lastAutoSyncAt.value < interval) return

  autoSyncing.value = true
  try {
    const loaders = new Map<string, () => Promise<unknown>>()
    // 任务和 AI job 是全局运行态来源，当前页面之外的异步变化也要持续感知。
    loaders.set('tasks', loadTasks)
    loaders.set('ai', loadAiJobs)
    if (isTrafficView.value) loaders.set('traffic-shell', loadTrafficShell)

    if (activeView.value === 'logs') loaders.set('selected-task', refreshSelectedTask)
    if (activeView.value === 'overview') loaders.set('overview', loadOverview)
    if (activeView.value === 'message-workbench') loaders.set('message-workbench', () => loadMessageWorkbench(true))
    if (activeView.value === 'tables') loaders.set('table', () => loadTable(activeLibrary.value, true))
    if (activeView.value === 'settings') {
      // 设置页有未保存草稿时，不用后台刷新覆盖本地输入。
      if (!settingsDraftDirty.value) loaders.set('settings', loadSettings)
      loaders.set('env', checkEnv)
      loaders.set('tombstones-summary', loadTombstoneSummary)
      loaders.set('tombstones', () => loadTombstones())
    }

    await Promise.allSettled(Array.from(loaders.values()).map(loader => loader()))
  } finally {
    lastAutoSyncAt.value = Date.now()
    autoSyncing.value = false
  }
}

async function changeLibrary(library: string) {
  activeLibrary.value = library
  tableStatus.value = ''
  tableKeyword.value = ''
  await loadTable(library)
}

async function changeTableFilter(filters: Dict) {
  tableStatus.value = filters.status || ''
  tableKeyword.value = filters.keyword || ''
  await loadTable(activeLibrary.value)
}

async function createTask(payload: Dict) {
  try {
    const { data } = await api.post('/tasks', payload)
    ElMessage.success(`任务 ${data.id} 已创建`)
    await loadTasks()
    selectedTask.value = await fetchTask(data.id)
    await loadSelectedTaskDiagnostics(data.id)
    await router.push('/logs')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '任务创建失败')
  }
}

async function openTaskLogs(id: string) {
  selectedTask.value = await fetchTask(id)
  await loadSelectedTaskDiagnostics(id)
  await router.push('/logs')
}

async function selectTask(id: string) {
  selectedTask.value = await fetchTask(id)
  await loadSelectedTaskDiagnostics(id)
}

async function archiveTask(id: string) {
  await api.post(`/tasks/${id}/archive`)
  ElMessage.success('任务已归档')
  await loadTasks()
}

async function cancelTask(id: string) {
  await api.post(`/tasks/${id}/cancel`)
  ElMessage.success('任务已取消')
  await Promise.allSettled([loadTasks(), loadOverview(), loadAiJobs()])
  selectedTask.value = await fetchTask(id)
  await loadSelectedTaskDiagnostics(id)
}

function retryTask(task: Dict) {
  retryDraft.value = { ...task, retry_token: Date.now() }
  router.push('/tasks')
  ElMessage.success('已带入失败任务参数，请确认后重新启动')
}

function consumeRetryDraft() {
  retryDraft.value = null
}

async function deleteTask(id: string) {
  await ElMessageBox.confirm('任务删除会同步删除项目库和 MyCrawler 底层映射数据。确认继续？', '硬删除确认', { type: 'warning' })
  await api.delete(`/tasks/${id}`)
  ElMessage.success('任务已硬删除')
  selectedTask.value = null
  taskDiagnostics.value = {}
  taskDedupSummary.value = {}
  await Promise.allSettled([loadTasks(), loadOverview(), loadAiJobs()])
}

async function updateRow(library: string, row: Dict) {
  await api.patch(`/tables/${library}/${row.id}`, { values: row })
  ElMessage.success('已保存')
  await loadTable(library)
}

async function deleteRow(library: string, row: Dict, hard?: boolean) {
  const message = library === 'target_customers' && !hard ? '目标客户会先隐藏并记录状态事件。确认删除？' : '此操作会删除项目库数据，并记录防重复墓碑。确认继续？'
  await ElMessageBox.confirm(message, '删除确认', { type: 'warning' })
  await api.delete(`/tables/${library}/${row.id}`, { params: { hard } })
  ElMessage.success('删除完成')
  await loadTable(library)
}

async function createAiJob(targetType: string, targetId: number) {
  try {
    await api.post('/ai/jobs', { target_type: targetType, target_id: targetId, run_now: true })
    ElMessage.success('AI分析完成')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || 'AI分析失败')
  } finally {
    await Promise.allSettled([loadAiJobs(), loadTable(activeLibrary.value), loadOverview()])
  }
}

async function confirmBulkPreview(payload: Dict, title = '批量操作预览') {
  const { data } = await api.post('/bulk-actions/preview', payload)
  await ElMessageBox.confirm(renderBulkPreviewMessage(data), title, {
    type: (data.tombstone_counts && Object.keys(data.tombstone_counts).length) ? 'warning' : 'info',
    confirmButtonText: '确认执行',
    cancelButtonText: '取消',
    customClass: 'bulk-preview-message-box',
  })
  return data
}

function renderBulkPreviewMessage(data: Dict) {
  return h('div', { class: 'bulk-preview-message' }, [
    h('p', { class: 'bulk-preview-confirm' }, data.confirm_text || '确认执行当前批量操作？'),
    h('div', { class: 'bulk-preview-metrics' }, [
      bulkPreviewMetric('符合条件', data.eligible_count || 0, 'success'),
      bulkPreviewMetric('跳过', data.skipped_count || 0, 'muted'),
    ]),
    bulkPreviewCountSection('预计影响', data.affected_counts || {}),
    bulkPreviewCountSection('预计写入墓碑', data.tombstone_counts || {}),
    bulkPreviewSamples(data.sample_rows || []),
    bulkPreviewWarnings(data.warnings || []),
  ].filter(Boolean))
}

function bulkPreviewMetric(label: string, value: unknown, tone = '') {
  return h('div', { class: ['bulk-preview-metric', tone ? `is-${tone}` : ''] }, [
    h('small', label),
    h('strong', String(value)),
  ])
}

function bulkPreviewCountSection(title: string, counts: Dict) {
  const entries = Object.entries(counts).filter(([, value]) => Number(value) > 0)
  return h('section', { class: 'bulk-preview-section' }, [
    h('h4', title),
    entries.length
      ? h('div', { class: 'bulk-preview-counts' }, entries.map(([key, value]) => (
        h('span', { class: 'bulk-preview-count' }, [
          h('em', bulkPreviewLabel(key)),
          h('strong', String(value)),
        ])
      )))
      : h('span', { class: 'bulk-preview-empty' }, '无'),
  ])
}

function bulkPreviewSamples(rows: Dict[]) {
  if (!rows.length) return null
  return h('section', { class: 'bulk-preview-section' }, [
    h('h4', '样例对象'),
    h('ul', { class: 'bulk-preview-samples' }, rows.map((row) => (
      h('li', [
        h('span', { title: String(row.name || row.id || '-') }, row.name || row.id || '-'),
        row.status ? h('em', row.status) : null,
      ])
    ))),
  ])
}

function bulkPreviewWarnings(items: string[]) {
  if (!items.length) return null
  return h('section', { class: 'bulk-preview-warning' }, [
    h('h4', '注意'),
    h('ul', items.map((item) => h('li', item))),
  ])
}

function bulkPreviewLabel(key: string) {
  const labels: Dict = {
    accounts: '账号',
    author_account: '作者账号墓碑',
    comments: '评论',
    comment: '评论墓碑',
    contents: '内容',
    content: '内容墓碑',
    leads: '线索',
    analysis_jobs: 'AI任务',
  }
  return labels[key] || key
}

async function createBatchAiJobs(targetType: string, targetIds: number[]) {
  const ids = Array.from(new Set(targetIds.map(Number).filter(Boolean)))
  if (!ids.length) {
    ElMessage.info('当前筛选范围没有可分析对象')
    return
  }
  try {
    await confirmBulkPreview({ action: 'ai_analyze', target_type: targetType, target_ids: ids }, 'AI批量分析预览')
    const { data } = await api.post('/ai/jobs/batch', { target_type: targetType, target_ids: ids, run_now: true })
    ElMessage.success(`已完成 ${data.length} 个AI分析任务`)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '批量AI分析失败')
  } finally {
    await Promise.allSettled([loadAiJobs(), loadTable(activeLibrary.value), loadOverview()])
  }
}

async function deleteAiWorkbenchNonCompetitors(targetIds: number[]) {
  const ids = Array.from(new Set(targetIds.map(Number).filter(Boolean)))
  if (!ids.length) {
    ElMessage.info('当前筛选范围没有可删除的非竞品')
    return
  }
  try {
    await confirmBulkPreview({ action: 'delete_non_competitors', target_type: 'competitor', target_ids: ids }, '删除非竞品预览')
    const { data } = await api.post('/ai/workbench/non-competitors/delete', { target_ids: ids })
    if (data.deleted) ElMessage.success(`已删除 ${data.deleted} 个非竞品账号`)
    else ElMessage.info('没有删除任何非竞品账号')
    if (data.skipped?.length) ElMessage.warning(`已跳过 ${data.skipped.length} 个不符合删除条件的账号`)
    if (data.failed?.length) ElMessage.error(`有 ${data.failed.length} 个账号删除失败`)
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error?.response?.data?.detail || '删除非竞品失败')
  } finally {
    await Promise.allSettled([loadAiJobs(), loadOverview(), loadTable(activeLibrary.value)])
  }
}

async function deleteAiWorkbenchNonCustomers(targetIds: number[]) {
  const ids = Array.from(new Set(targetIds.map(Number).filter(Boolean)))
  if (!ids.length) {
    ElMessage.info('当前筛选范围没有可删除的非客户')
    return
  }
  try {
    await confirmBulkPreview({ action: 'delete_non_customers', target_type: 'lead', target_ids: ids }, '删除非客户预览')
    const { data } = await api.post('/ai/workbench/non-customers/delete', { target_ids: ids })
    if (data.deleted) ElMessage.success(`已删除 ${data.deleted} 个非客户`)
    else ElMessage.info('没有删除任何非客户')
    if (data.skipped?.length) ElMessage.warning(`已跳过 ${data.skipped.length} 个不符合删除条件的客户`)
    if (data.failed?.length) ElMessage.error(`有 ${data.failed.length} 个客户删除失败`)
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error?.response?.data?.detail || '删除非客户失败')
  } finally {
    await Promise.allSettled([loadAiJobs(), loadOverview(), loadTable(activeLibrary.value)])
  }
}

async function retryAiJob(jobId: string) {
  try {
    await api.post(`/ai/jobs/${jobId}/retry`)
    ElMessage.success('重试完成')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '重试失败')
  } finally {
    await Promise.allSettled([loadAiJobs(), loadTable(activeLibrary.value), loadOverview()])
  }
}

async function retryAiJobs(jobIds: string[]) {
  const ids = Array.from(new Set(jobIds.map(String).filter(Boolean)))
  if (!ids.length) {
    ElMessage.info('当前没有可重试的失败任务')
    return
  }
  try {
    await confirmBulkPreview({ action: 'retry_failed_ai', target_type: 'ai_job', target_ids: ids }, 'AI失败重试预览')
    await Promise.all(ids.map(id => api.post(`/ai/jobs/${id}/retry`)))
    ElMessage.success(`已重试 ${ids.length} 个AI任务`)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '批量重试失败')
  } finally {
    await Promise.allSettled([loadAiJobs(), loadTable(activeLibrary.value), loadOverview()])
  }
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
    await Promise.all([loadTasks(), loadOverview()])
    selectedTask.value = await fetchTask(data.id)
    await router.push('/logs')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '补资料任务创建失败')
  }
}

async function analyzeOverviewAccount(node: Dict) {
  const accountId = node.metrics?.id
  if (!accountId) {
    ElMessage.error('当前账号缺少ID，无法分析')
    return
  }
  try {
    const { data } = await api.post(`/accounts/${accountId}/analysis`)
    ElMessage.success(`账号分析任务 ${data.id} 已创建，采集完成后会自动执行AI判断`)
    await Promise.allSettled([loadTasks(), loadOverview(), loadAiJobs()])
    selectedTask.value = await fetchTask(data.id)
    await router.push('/logs')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '账号分析任务创建失败')
  }
}

async function analyzeOverviewCustomerIntent(node: Dict) {
  const leadId = node.metrics?.lead_id || String(node.id || '').split(':')[1]
  if (!leadId) {
    ElMessage.error('当前客户缺少线索ID，无法意向分析')
    return
  }
  try {
    await api.post(`/overview/customers/${leadId}/intent-analysis`)
    ElMessage.success('客户意向分析完成')
    await Promise.allSettled([loadOverview(), loadAiJobs(), loadTable(activeLibrary.value)])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '客户意向分析失败')
  }
}

async function updateOverviewCustomerFollowStatus(node: Dict, status: string) {
  const leadId = node.metrics?.lead_id || node.metrics?.id || String(node.id || '').split(':')[1]
  if (!leadId) {
    ElMessage.error('当前客户缺少线索ID，无法修改跟进状态')
    return
  }
  const currentStatus = String(node.metrics?.follow_status || node.metrics?.screening_status || '待筛选')
  if (currentStatus === status) return
  try {
    if (['已成交', '未成交'].includes(currentStatus)) {
      await ElMessageBox.confirm(`当前客户已经是“${currentStatus}”，确认改为“${status}”？`, '修改跟进状态', { type: 'warning' })
    }
    const note = `人工修改跟进状态：${currentStatus} -> ${status}`
    await api.patch(`/overview/customers/${leadId}/follow-status`, { follow_status: status, note })
    ElMessage.success(`跟进状态已更新为“${status}”`)
    await Promise.allSettled([loadOverview(), loadTable(activeLibrary.value), loadAiJobs()])
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error?.response?.data?.detail || '跟进状态修改失败')
  }
}

function selectedDmScript(aiScript: unknown) {
  const fixedMode = String(settings.value.dm_script_mode || 'ai') === 'fixed'
  if (fixedMode) {
    return {
      text: String(settings.value.fixed_dm_script || '').trim(),
      label: '固定话术',
      emptyMessage: '固定话术为空，请先到设置页填写固定话术',
    }
  }
  return {
    text: String(aiScript || '').trim(),
    label: 'AI话术',
    emptyMessage: '当前客户暂无AI话术，请先做意向分析',
  }
}

async function messageOverviewCustomer(node: Dict) {
  const leadId = node.metrics?.lead_id || node.metrics?.id || String(node.id || '').split(':')[1]
  const scriptSelection = selectedDmScript(node.metrics?.script)
  const profileUrl = String(node.metrics?.profile_url || '').trim()
  if (!leadId) {
    ElMessage.error('当前客户缺少线索ID，无法标记私信')
    return
  }
  if (!scriptSelection.text) {
    ElMessage.error(scriptSelection.emptyMessage)
    return
  }
  if (!profileUrl) {
    ElMessage.error('当前客户缺少主页链接，无法打开主页')
    return
  }
  try {
    const homepage = window.open(profileUrl, '_blank')
    if (!homepage) {
      ElMessage.warning('浏览器拦截了主页窗口，未复制话术，也未修改跟进状态')
      return
    }
    homepage.opener = null
    await navigator.clipboard.writeText(scriptSelection.text)

    const currentStatus = String(node.metrics?.follow_status || node.metrics?.screening_status || '待筛选')
    const shouldMarkMessaged = ['待筛选', '未分析', '目标客户', '未私信'].includes(currentStatus)
    if (shouldMarkMessaged) {
      await api.patch(`/overview/customers/${leadId}/follow-status`, {
        follow_status: '已私信',
        note: `点击私信按钮：复制${scriptSelection.label}并打开客户主页`
      })
      ElMessage.success(`${scriptSelection.label}已复制，客户主页已打开，跟进状态已更新为“已私信”`)
    } else {
      ElMessage.success(`${scriptSelection.label}已复制，客户主页已打开；当前状态“${currentStatus}”未回退`)
    }
    await Promise.allSettled([loadOverview(), loadTable(activeLibrary.value), loadAiJobs()])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '私信操作失败')
  }
}

async function changeMessageWorkbenchFilter(filters: Dict) {
  messageFilters.value = {
    ...messageFilters.value,
    ...filters,
    page: Number(filters.page || 1),
  }
  await loadMessageWorkbench()
}

async function selectMessageWorkbenchCustomer(leadId: number | string) {
  const { data } = await api.get(`/message-workbench/customers/${leadId}`)
  messageDetail.value = data
}

function closeMessageWorkbenchDetail() {
  messageDetail.value = {}
}

async function updateMessageWorkbenchFollowStatus(row: Dict, status: string) {
  const leadId = row.lead_id || row.id
  if (!leadId) {
    ElMessage.error('当前客户缺少线索ID，无法修改状态')
    return
  }
  try {
    await api.patch(`/overview/customers/${leadId}/follow-status`, {
      follow_status: status,
      note: '私信工作台修改跟进状态'
    })
    ElMessage.success(`已更新为“${status}”`)
    await Promise.allSettled([loadMessageWorkbench(true), loadOverview(), loadAiJobs(), loadTable(activeLibrary.value, true)])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '修改跟进状态失败')
  }
}

async function messageWorkbenchCustomer(row: Dict) {
  const leadId = row.lead_id || row.id
  const scriptSelection = selectedDmScript(row.script)
  const profileUrl = String(row.profile_url || '').trim()
  if (!leadId) {
    ElMessage.error('当前客户缺少线索ID，无法标记私信')
    return
  }
  if (!scriptSelection.text) {
    ElMessage.error(scriptSelection.emptyMessage)
    return
  }
  if (!profileUrl) {
    ElMessage.error('当前客户缺少主页链接，无法打开主页')
    return
  }
  try {
    const homepage = window.open(profileUrl, '_blank')
    if (!homepage) {
      ElMessage.warning('浏览器拦截了主页窗口，未复制话术，也未修改跟进状态')
      return
    }
    homepage.opener = null
    await navigator.clipboard.writeText(scriptSelection.text)

    const currentStatus = String(row.follow_status || row.screening_status || '未私信')
    const shouldMarkMessaged = ['待筛选', '未分析', '目标客户', '未私信'].includes(currentStatus)
    if (shouldMarkMessaged) {
      await api.patch(`/overview/customers/${leadId}/follow-status`, {
        follow_status: '已私信',
        note: `私信工作台：复制${scriptSelection.label}并打开客户主页`
      })
      ElMessage.success(`${scriptSelection.label}已复制，客户主页已打开，跟进状态已更新为“已私信”`)
    } else {
      ElMessage.success(`${scriptSelection.label}已复制，客户主页已打开；当前状态“${currentStatus}”未回退`)
    }
    await Promise.allSettled([loadMessageWorkbench(true), loadOverview(), loadAiJobs(), loadTable(activeLibrary.value, true)])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '私信操作失败')
  }
}

async function autoMessageWorkbenchCustomer(row: Dict) {
  const leadId = row.lead_id || row.id
  const scriptSelection = selectedDmScript(row.script)
  if (!leadId) {
    ElMessage.error('当前客户缺少线索ID，无法自动私信')
    return
  }
  if (row.platform !== 'dy') {
    ElMessage.error('自动私信当前只支持抖音客户')
    return
  }
  if (!scriptSelection.text) {
    ElMessage.error(scriptSelection.emptyMessage)
    return
  }
  try {
    const { data } = await api.post(`/message-workbench/customers/${leadId}/auto-message`, {
      dry_run: Boolean(settings.value.auto_dm_fill_only),
      timeout_seconds: Number(settings.value.auto_dm_timeout_seconds || 0),
      message_script: scriptSelection.text,
      script_label: scriptSelection.label
    })
    ElMessage.success(data?.dm?.note || '自动私信已完成，跟进状态已同步')
    await Promise.allSettled([loadMessageWorkbench(true), loadOverview(), loadAiJobs(), loadTable(activeLibrary.value, true)])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '自动私信失败')
  }
}

async function startMessageAutoBatch(payload: Dict) {
  if (String(payload.platform || '') !== 'dy') {
    ElMessage.info('AI一键私信已跳过快手/小红书平台，请选择抖音关键词')
    return
  }
  try {
    const { data } = await api.post('/message-workbench/auto-message-batches', payload)
    ElMessage.success(`自动私信批次 ${data.id} 已启动`)
    await loadMessageWorkbench(true)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '启动自动私信批次失败')
  }
}

async function cancelMessageAutoBatch(batch: Dict) {
  const batchId = batch?.id
  if (!batchId) {
    ElMessage.error('当前批次缺少ID，无法取消')
    return
  }
  try {
    await api.post(`/message-workbench/auto-message-batches/${batchId}/cancel`)
    ElMessage.success('已请求取消自动私信批次')
    await loadMessageWorkbench(true)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '取消自动私信批次失败')
  }
}

async function retryMessageAutoBatch(batch: Dict) {
  const batchId = batch?.id
  if (!batchId) {
    ElMessage.error('当前批次缺少ID，无法重试')
    return
  }
  try {
    const { data } = await api.post(`/message-workbench/auto-message-batches/${batchId}/retry`)
    ElMessage.success(`已创建重试批次 ${data.id}`)
    await loadMessageWorkbench(true)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '重试自动私信批次失败')
  }
}

async function deleteMessageAutoBatch(batch: Dict) {
  const batchId = batch?.id
  if (!batchId) {
    ElMessage.error('当前批次缺少ID，无法删除')
    return
  }
  try {
    await ElMessageBox.confirm(`只删除自动私信批次 ${batchId} 的历史记录，不会删除客户数据。确认继续？`, '删除批次记录', { type: 'warning' })
    await api.delete(`/message-workbench/auto-message-batches/${batchId}`)
    ElMessage.success('自动私信批次记录已删除')
    await loadMessageWorkbench(true)
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error?.response?.data?.detail || '删除自动私信批次失败')
  }
}

async function analyzeAccountCustomersIntent(node: Dict) {
  const accountId = node.metrics?.id
  if (!accountId) {
    ElMessage.error('当前竞品账号缺少ID，无法一键意向分析')
    return
  }
  try {
    const { data } = await api.post(`/overview/accounts/${accountId}/customers/analyze`)
    const runnableCount = Number(data.created || 0) + Number(data.resumed || 0)
    if (runnableCount) {
      ElMessage.success(`已创建 ${data.created || 0} 个、恢复 ${data.resumed || 0} 个客户意向分析任务，并行数 ${data.concurrency || 1}`)
    } else if (data.lead_count) {
      ElMessage.info(`客户账号暂无可启动的意向分析任务，跳过 ${data.skipped?.length || 0} 个运行中任务`)
    } else {
      ElMessage.info('当前竞品账号下没有可分析的客户账号')
    }
    await Promise.allSettled([loadOverview(), loadAiJobs(), loadTable(activeLibrary.value)])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '一键意向分析失败')
  }
}

async function analyzeKeywordCompetitors(node: Dict) {
  const { platform, keyword } = overviewKeywordScope(node)
  if (!platform || !keyword) {
    ElMessage.error('当前关键词缺少平台或关键词信息，无法分析')
    return
  }
  if (!['dy', 'xhs', 'ks'].includes(platform)) {
    ElMessage.error('当前平台不支持主页资料采集，无法批量账号分析')
    return
  }
  const pendingCount = (node.children || []).filter((child: Dict) => competitorStatusLabel(child.metrics?.competitor_status) === '未分析').length
  try {
    await confirmBulkPreview({ action: 'keyword_analyze', target_type: 'keyword', filters: { platform, keyword } }, '一键竞品分析预览')
    const { data } = await api.post('/overview/keywords/analyze', null, { params: { platform, keyword } })
    if (data.created) ElMessage.success(`已创建 1 个账号分析任务，包含 ${data.account_count || 0} 个未分析账号`)
    else if (pendingCount) ElMessage.info('未分析账号已有运行中的账号分析任务')
    else ElMessage.info('当前关键词下没有未分析账号')
    await Promise.allSettled([loadTasks(), loadOverview(), loadAiJobs()])
    if (data.task_ids?.length) {
      selectedTask.value = await fetchTask(data.task_ids[0])
      await router.push('/logs')
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '一键竞品分析失败')
  }
}

async function deleteAccountNonCustomers(node: Dict) {
  const accountId = node.metrics?.id
  if (!accountId) {
    ElMessage.error('当前账号缺少ID，无法删除非客户')
    return
  }
  try {
    await confirmBulkPreview({ action: 'delete_non_customers', target_type: 'lead', filters: { source_account_id: accountId } }, '删除非客户预览')
    const { data } = await api.post(`/overview/accounts/${accountId}/customers/non-customers/delete`)
    if (data.deleted) ElMessage.success(`已删除 ${data.deleted} 个非客户`)
    else ElMessage.info('当前账号下没有可删除的非客户')
    if (data.failed) ElMessage.warning(`有 ${data.failed} 个非客户删除失败，请查看返回错误`)
    await Promise.allSettled([loadOverview(), loadTable(activeLibrary.value), loadTasks(), loadAiJobs()])
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error?.response?.data?.detail || '删除非客户失败')
  }
}

async function deleteOverviewAccount(node: Dict) {
  const accountId = node.metrics?.id
  if (!accountId) {
    ElMessage.error('当前账号缺少ID，无法删除')
    return
  }
  try {
    await ElMessageBox.confirm('此操作会删除该账号相关内容、评论、线索来源和可清理账号，并同步 MyCrawler 底层映射。确认删除该账号？', '删除账号确认', { type: 'warning' })
    const { data } = await api.delete(`/overview/accounts/${accountId}`)
    ElMessage.success(scopeDeleteMessage('账号数据已删除', data))
    await Promise.allSettled([loadOverview(), loadTable(activeLibrary.value), loadTasks()])
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error?.response?.data?.detail || '账号删除失败')
  }
}

async function deleteOverviewCustomer(node: Dict) {
  const leadId = node.metrics?.id
  if (!leadId) {
    ElMessage.error('当前客户缺少线索ID，无法删除')
    return
  }
  try {
    await ElMessageBox.confirm('此操作会删除该客户账号、评论证据和相关线索来源，并记录防重复墓碑。确认删除该客户？', '删除客户确认', { type: 'warning' })
    const sourceAccountId = node.metrics?.source_account_id
    const { data } = await api.delete(`/overview/customers/${leadId}`, {
      params: sourceAccountId ? { source_account_id: sourceAccountId } : {}
    })
    ElMessage.success(scopeDeleteMessage('客户数据已删除', data))
    await Promise.allSettled([loadOverview(), loadTable(activeLibrary.value), loadTasks()])
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error?.response?.data?.detail || '客户删除失败')
  }
}

async function deleteKeywordNonCompetitors(node: Dict) {
  const { platform, keyword } = overviewKeywordScope(node)
  if (!platform || !keyword) {
    ElMessage.error('当前关键词缺少平台或关键词信息，无法删除')
    return
  }
  try {
    await confirmBulkPreview({ action: 'delete_non_competitors', target_type: 'competitor', filters: { platform, keyword } }, '一键删除非竞品预览')
    const { data } = await api.post('/overview/keywords/non-competitors/delete', null, { params: { platform, keyword } })
    if (data.deleted) ElMessage.success(`已删除 ${data.deleted} 个非竞品账号`)
    else ElMessage.info('当前关键词下没有可删除的非竞品账号')
    await Promise.allSettled([loadOverview(), loadTable(activeLibrary.value)])
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error?.response?.data?.detail || '一键删除非竞品失败')
  }
}

async function deleteOverviewPlatform(node: Dict) {
  const platform = node.metrics?.platform || node.label
  if (!platform) {
    ElMessage.error('当前平台缺少平台信息，无法删除')
    return
  }
  try {
    await ElMessageBox.confirm(`将硬删除“${platformName(platform)}”平台下的内容、评论、线索、账号来源，并同步 MyCrawler 底层映射。确认继续？`, '删除平台数据', { type: 'warning' })
    const { data } = await api.delete(`/overview/platforms/${encodeURIComponent(platform)}`)
    ElMessage.success(scopeDeleteMessage('平台数据已删除', data))
    await Promise.allSettled([loadOverview(), loadTable(activeLibrary.value), loadTasks()])
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error?.response?.data?.detail || '平台数据删除失败')
  }
}

async function deleteOverviewKeyword(node: Dict) {
  const { platform, keyword } = overviewKeywordScope(node)
  if (!platform || !keyword) {
    ElMessage.error('当前关键词缺少平台或关键词信息，无法删除')
    return
  }
  try {
    await ElMessageBox.confirm(`将硬删除“${platformName(platform)} / ${keyword}”关键词下的内容、评论、线索、账号来源，并同步 MyCrawler 底层映射。确认继续？`, '删除关键词数据', { type: 'warning' })
    const { data } = await api.delete('/overview/keywords', { params: { platform, keyword } })
    ElMessage.success(scopeDeleteMessage('关键词数据已删除', data))
    await Promise.allSettled([loadOverview(), loadTable(activeLibrary.value), loadTasks()])
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(error?.response?.data?.detail || '关键词数据删除失败')
  }
}

function overviewKeywordScope(node: Dict) {
  const idParts = String(node.id || '').split(':')
  const idPlatform = idParts[0] === 'keyword' ? idParts[1] || '' : ''
  const idKeyword = idParts[0] === 'keyword' ? idParts.slice(2).join(':') : ''
  return {
    platform: String(node.metrics?.platform || idPlatform || '').trim(),
    keyword: String(node.metrics?.keyword || node.label || idKeyword || '').trim(),
  }
}

function scopeDeleteMessage(prefix: string, data: Dict) {
  const counts = data.counts || {}
  return `${prefix}：内容 ${counts.contents || 0} / 评论 ${counts.comments || 0} / 账号 ${counts.accounts || 0} / 线索 ${counts.leads || 0}`
}

async function findCustomers(target: Dict) {
  try {
    let data: Dict
    if (target.kind === 'keyword') {
      const { platform, keyword } = overviewKeywordScope(target)
      if (!platform || !keyword) {
        ElMessage.error('当前关键词缺少平台或关键词信息，无法找客户')
        return
      }
      await confirmBulkPreview({ action: 'keyword_find_customers', target_type: 'keyword', filters: { platform, keyword } }, '一键找客户预览')
      const response = await api.post('/overview/keywords/find-customers', null, { params: { platform, keyword } })
      data = response.data
    } else {
      const accountId = target.metrics?.id || target.id
      if (!accountId) {
        ElMessage.error('当前账号缺少ID，无法找客户')
        return
      }
      const response = await api.post(`/accounts/${accountId}/find-customers`)
      data = response.data
    }

    if (data.created) {
      const taskCount = data.task_ids?.length || data.created || 0
      const reuseCount = data.reuse_content_count || 0
      const supplementCount = data.creator_account_count || 0
      const recentSkipCount = data.recent_comment_skip_count || 0
      const skipContentCount = data.skip_content_count || 0
      const supplementTarget = data.supplement_content_count || 0
      ElMessage.success(`已创建 ${taskCount} 个找客户任务：复用已有内容 ${reuseCount} 条，补采账号 ${supplementCount} 个，新内容目标 ${supplementTarget} 条，跳过已采内容 ${skipContentCount} 条，近期评论跳过 ${recentSkipCount} 条`)
    } else {
      if (data.recent_comment_skip_count) {
        ElMessage.info(`近期已采过 ${data.recent_comment_skip_count} 条内容的评论，未重复创建采集任务`)
      } else {
        ElMessage.info(data.skipped?.length ? '相关账号已有运行中的找客户任务' : '没有可用于找客户的账号')
      }
    }
    await Promise.allSettled([loadTasks(), loadOverview(), loadAiJobs()])
    if (data.task_ids?.length) {
      selectedTask.value = await fetchTask(data.task_ids[0])
      await router.push('/logs')
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '找客户任务创建失败')
  }
}

async function saveSettings(values: Dict) {
  settingsSaving.value = true
  settingsMutationSeq += 1
  try {
    const { data } = await api.put('/settings', { values })
    settings.value = data
    settingsDraftDirty.value = false
    settingsSaveRevision.value += 1
    ElMessage.success('设置已保存')
    await checkEnv()
  } finally {
    settingsSaving.value = false
  }
}

async function clearAllData() {
  let value = ''
  try {
    const result = await ElMessageBox.prompt(
      '这会清空项目业务库、任务日志、AI结果、证据链，并清空当前设置指向的 MyCrawler SQLite 所有业务表内容。数据库文件和设置项会保留。请输入“清空所有数据”确认。',
      '清空所有数据',
      {
        confirmButtonText: '清空',
        cancelButtonText: '取消',
        inputPlaceholder: '清空所有数据',
        inputPattern: /^清空所有数据$/,
        inputErrorMessage: '必须输入“清空所有数据”',
        type: 'warning',
      },
    )
    value = String(result.value || '')
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    throw error
  }
  try {
    const { data } = await api.post('/settings/clear-data', { confirm: value })
    ElMessage.success(`已清空数据：项目表 ${data.project_tables || 0} 个，底层表 ${data.raw_tables || 0} 个`)
    selectedTask.value = null
    await refreshAll()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '清空所有数据失败')
  }
}

watch(activeView, () => {
  void syncCurrentView('route')
})

onMounted(async () => {
  await refreshAll()
  startAutoSync()
  document.addEventListener('visibilitychange', handleVisibilityChange)
})

onBeforeUnmount(() => {
  stopAutoSync()
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})
</script>
