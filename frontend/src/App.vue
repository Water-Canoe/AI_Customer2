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
      <el-menu :default-active="activeView" class="nav" @select="goToView">
        <el-menu-item index="tasks"><el-icon><Operation /></el-icon><span>任务管理</span></el-menu-item>
        <el-menu-item index="overview"><el-icon><Share /></el-icon><span>总览树</span></el-menu-item>
        <el-menu-item index="ai"><el-icon><MagicStick /></el-icon><span>AI分析</span></el-menu-item>
        <el-menu-item index="message-workbench"><el-icon><Message /></el-icon><span>私信工作台</span></el-menu-item>
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
          <el-tag type="info" effect="plain">
            {{ autoSyncHint }}
          </el-tag>
          <el-tag :type="envReady ? 'success' : 'warning'" effect="light">
            {{ envReady ? '环境就绪' : '需要检查环境' }}
          </el-tag>
          <el-button :icon="Refresh" @click="refreshAll">刷新</el-button>
          <el-button type="primary" :icon="Plus" @click="router.push('/tasks')">新建任务</el-button>
        </div>
      </el-header>

      <el-main class="main" :class="`view-${activeView}`">
        <section class="workflow-strip">
          <button
            v-for="step in workflowSteps"
            :key="step.title"
            class="workflow-step"
            :class="{ active: step.view === activeView }"
            type="button"
            @click="router.push(step.route)"
          >
            <span>{{ step.index }}</span>
            <div>
              <strong>{{ step.title }}</strong>
              <small>{{ step.note }}</small>
            </div>
          </button>
        </section>

        <RouterView v-slot="{ Component }">
          <component :is="Component" v-bind="routeProps" v-on="routeListeners" />
        </RouterView>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import {
  Grid,
  MagicStick,
  Message,
  Operation,
  Plus,
  Refresh,
  Setting,
  Share,
  Tickets,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { api } from './shared/api'
import type { Dict } from './shared/types'
import { competitorStatusLabel, platformName } from './shared/format'
import { workflowSteps } from './router'

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
const env = ref<Dict>({})
const platformCapabilities = ref<Dict[]>([])
const messageKeywords = ref<Dict[]>([])
const messageCustomers = ref<Dict>({ rows: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
const messageDetail = ref<Dict>({})
const messageLoading = ref(false)
const messageFilters = ref<Dict>({ keyword: '', status: '待私信', query: '', page: 1, page_size: 20 })
const tombstoneSummary = ref<Dict>({})
const tombstones = ref<Dict>({ items: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
const tombstoneFilters = ref<Dict>({ entity_type: '', platform: '', source: '', query: '', page: 1, page_size: 20 })
const autoSyncing = ref(false)
const lastAutoSyncAt = ref(0)

const AUTO_SYNC_ACTIVE_MS = 3000
const AUTO_SYNC_IDLE_MS = 12000
let autoSyncTimer: ReturnType<typeof window.setInterval> | null = null

const activeView = computed(() => String(route.name || 'tasks'))
const viewTitle = computed(() => String(route.meta.title || '任务管理'))
const viewSubtitle = computed(() => String(route.meta.subtitle || ''))
const workflowHint = computed(() => String(route.meta.hint || ''))
const envReady = computed(() => Boolean(env.value?.media_crawler_path?.ok && env.value?.media_crawler_db?.ok))
const hasActiveAsyncWork = computed(() => {
  return tasks.value.some(task => isActiveStatus(task.status)) || aiJobs.value.some(job => isActiveStatus(job.status))
})
const autoSyncHint = computed(() => {
  const seconds = Math.round((hasActiveAsyncWork.value ? AUTO_SYNC_ACTIVE_MS : AUTO_SYNC_IDLE_MS) / 1000)
  return autoSyncing.value ? '同步中' : `自动同步 ${seconds}s`
})

const routeProps = computed(() => {
  if (activeView.value === 'tasks') {
    return {
      tasks: tasks.value,
      settings: settings.value,
      capabilities: platformCapabilities.value,
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
  return {
    settings: settings.value,
    env: env.value,
    tombstoneSummary: tombstoneSummary.value,
    tombstones: tombstones.value,
    tombstoneFilters: tombstoneFilters.value,
  }
})

const routeListeners = computed(() => ({
  'create-task': createTask,
  'open-logs': openTaskLogs,
  'consume-retry-draft': consumeRetryDraft,
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
  'create-job': createAiJob,
  'create-batch-jobs': createBatchAiJobs,
  'delete-non-competitors': deleteAiWorkbenchNonCompetitors,
  'delete-non-customers': deleteAiWorkbenchNonCustomers,
  'filter-change': changeMessageWorkbenchFilter,
  'select-customer': selectMessageWorkbenchCustomer,
  'message-customer': messageWorkbenchCustomer,
  'update-follow-status': updateMessageWorkbenchFollowStatus,
  'close-detail': closeMessageWorkbenchDetail,
  'retry-job': retryAiJob,
  'retry-jobs': retryAiJobs,
  'select-task': selectTask,
  'retry-task': retryTask,
  'cancel-task': cancelTask,
  'archive-task': archiveTask,
  'delete-task': deleteTask,
  'change-library': changeLibrary,
  'change-filter': changeTableFilter,
  'update-row': updateRow,
  'delete-row': deleteRow,
  'analyze-row': analyzeTableRow,
  'enrich-profile': enrichProfile,
  save: saveSettings,
  'check-env': checkEnv,
  'load-tombstones': loadTombstones,
  'clear-data': clearAllData,
}))

function goToView(view: string) {
  router.push(`/${view}`)
}

async function refreshAll() {
  // 首页各面板独立加载，单个接口失败时不阻塞其它工作区。
  await Promise.allSettled([loadTasks(), loadSettings(), checkEnv(), loadPlatformCapabilities(), loadAiJobs(), loadOverview(), loadMessageWorkbench(true), loadTombstoneSummary(), loadTombstones(), loadTable(activeLibrary.value)])
  lastAutoSyncAt.value = Date.now()
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

async function loadPlatformCapabilities() {
  const { data } = await api.get('/platform-capabilities')
  platformCapabilities.value = data
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
    const [keywords, customers] = await Promise.all([
      api.get('/message-workbench/keywords'),
      api.get('/message-workbench/customers', { params: messageFilters.value })
    ])
    messageKeywords.value = keywords.data
    messageCustomers.value = customers.data
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

    if (activeView.value === 'logs') loaders.set('selected-task', refreshSelectedTask)
    if (activeView.value === 'overview') loaders.set('overview', loadOverview)
    if (activeView.value === 'message-workbench') loaders.set('message-workbench', () => loadMessageWorkbench(true))
    if (activeView.value === 'tables') loaders.set('table', () => loadTable(activeLibrary.value, true))
    if (activeView.value === 'settings') {
      loaders.set('settings', loadSettings)
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
  await ElMessageBox.confirm('任务删除会同步删除项目库和 MediaCrawler 底层映射数据。确认继续？', '硬删除确认', { type: 'warning' })
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
  const affected = Object.entries(data.affected_counts || {})
    .map(([key, value]) => `${key} ${value}`)
    .join(' / ') || '无'
  const tombstonesText = Object.entries(data.tombstone_counts || {})
    .map(([key, value]) => `${key} ${value}`)
    .join(' / ') || '无'
  const samples = (data.sample_rows || [])
    .map((row: Dict) => `- ${row.name || row.id || '-'}${row.status ? `（${row.status}）` : ''}`)
    .join('\n')
  const warnings = (data.warnings || []).map((item: string) => `- ${item}`).join('\n')
  const message = [
    data.confirm_text || '确认执行当前批量操作？',
    `符合条件：${data.eligible_count || 0}`,
    `跳过：${data.skipped_count || 0}`,
    `预计影响：${affected}`,
    `预计写入墓碑：${tombstonesText}`,
    samples ? `样例：\n${samples}` : '',
    warnings ? `注意：\n${warnings}` : '',
  ].filter(Boolean).join('\n\n')
  await ElMessageBox.confirm(message, title, {
    type: (data.tombstone_counts && Object.keys(data.tombstone_counts).length) ? 'warning' : 'info',
    confirmButtonText: '确认执行',
    cancelButtonText: '取消',
  })
  return data
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

async function messageOverviewCustomer(node: Dict) {
  const leadId = node.metrics?.lead_id || node.metrics?.id || String(node.id || '').split(':')[1]
  const script = String(node.metrics?.script || '').trim()
  const profileUrl = String(node.metrics?.profile_url || '').trim()
  if (!leadId) {
    ElMessage.error('当前客户缺少线索ID，无法标记私信')
    return
  }
  if (!script) {
    ElMessage.error('当前客户暂无AI话术，请先做意向分析')
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
    await navigator.clipboard.writeText(script)

    const currentStatus = String(node.metrics?.follow_status || node.metrics?.screening_status || '待筛选')
    const shouldMarkMessaged = ['待筛选', '未分析', '目标客户', '未私信'].includes(currentStatus)
    if (shouldMarkMessaged) {
      await api.patch(`/overview/customers/${leadId}/follow-status`, {
        follow_status: '已私信',
        note: '点击私信按钮：复制AI话术并打开客户主页'
      })
      ElMessage.success('AI话术已复制，客户主页已打开，跟进状态已更新为“已私信”')
    } else {
      ElMessage.success(`AI话术已复制，客户主页已打开；当前状态“${currentStatus}”未回退`)
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
  const script = String(row.script || '').trim()
  const profileUrl = String(row.profile_url || '').trim()
  if (!leadId) {
    ElMessage.error('当前客户缺少线索ID，无法标记私信')
    return
  }
  if (!script) {
    ElMessage.error('当前客户暂无AI话术，请先做意向分析')
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
    await navigator.clipboard.writeText(script)

    const currentStatus = String(row.follow_status || row.screening_status || '未私信')
    const shouldMarkMessaged = ['待筛选', '未分析', '目标客户', '未私信'].includes(currentStatus)
    if (shouldMarkMessaged) {
      await api.patch(`/overview/customers/${leadId}/follow-status`, {
        follow_status: '已私信',
        note: '私信工作台：复制AI话术并打开客户主页'
      })
      ElMessage.success('AI话术已复制，客户主页已打开，跟进状态已更新为“已私信”')
    } else {
      ElMessage.success(`AI话术已复制，客户主页已打开；当前状态“${currentStatus}”未回退`)
    }
    await Promise.allSettled([loadMessageWorkbench(true), loadOverview(), loadAiJobs(), loadTable(activeLibrary.value, true)])
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '私信操作失败')
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
  if (!['dy', 'xhs'].includes(platform)) {
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
    ElMessage.error('当前竞品账号缺少ID，无法删除非客户')
    return
  }
  try {
    await confirmBulkPreview({ action: 'delete_non_customers', target_type: 'lead', filters: { source_account_id: accountId } }, '删除非客户预览')
    const { data } = await api.post(`/overview/accounts/${accountId}/customers/non-customers/delete`)
    if (data.deleted) ElMessage.success(`已删除 ${data.deleted} 个非客户`)
    else ElMessage.info('当前竞品账号下没有可删除的非客户')
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
    await ElMessageBox.confirm('此操作会删除该账号相关内容、评论、线索来源和可清理账号，并同步 MediaCrawler 底层映射。确认删除该账号？', '删除账号确认', { type: 'warning' })
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
    await ElMessageBox.confirm(`将硬删除“${platformName(platform)}”平台下的内容、评论、线索、账号来源，并同步 MediaCrawler 底层映射。确认继续？`, '删除平台数据', { type: 'warning' })
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
    await ElMessageBox.confirm(`将硬删除“${platformName(platform)} / ${keyword}”关键词下的内容、评论、线索、账号来源，并同步 MediaCrawler 底层映射。确认继续？`, '删除关键词数据', { type: 'warning' })
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
      ElMessage.success(`已创建找客户任务，包含 ${data.account_count || 0} 个竞品账号`)
    } else {
      ElMessage.info(data.skipped?.length ? '相关竞品账号已有运行中的找客户任务' : '没有可用于找客户的竞品账号')
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
