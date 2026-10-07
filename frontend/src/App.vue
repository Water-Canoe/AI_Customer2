<template>
  <el-container class="shell">
    <el-aside class="sidebar" width="224px">
      <div class="brand"><img class="brand-mark" src="/ai-customer-icon.png" alt="" /><div><strong>AI 获客</strong><span>本地运营工作台</span></div></div>
      <el-menu ref="sidebarMenu" :default-active="activeMenuItem" :default-openeds="[]" :unique-opened="true" class="nav" @select="goToView">
        <el-menu-item index="workspace"><el-icon><House /></el-icon><span>工作台</span></el-menu-item>
        <el-menu-item index="acquisition"><el-icon><Operation /></el-icon><span>获客工作台</span></el-menu-item>
        <el-menu-item index="message-workbench"><el-icon><Message /></el-icon><span>客户工作台</span></el-menu-item>
        <el-menu-item index="traffic-plans"><el-icon><Promotion /></el-icon><span>引流运营</span></el-menu-item>
        <el-sub-menu index="content-workbench"><template #title><el-icon><VideoPlay /></el-icon><span>内容</span></template>
          <el-menu-item index="content-create">视频创作</el-menu-item><el-menu-item index="content-assets">素材与音色</el-menu-item>
          <el-menu-item index="content-records">作品库</el-menu-item><el-menu-item index="content-publish">发布中心</el-menu-item>
        </el-sub-menu>
        <el-menu-item index="automation-plans"><el-icon><Clock /></el-icon><span>自动化计划</span></el-menu-item>
        <el-menu-item index="runtime-center"><el-icon><Monitor /></el-icon><span>任务中心</span></el-menu-item>
      </el-menu>
      <div class="sidebar-footer">
        <button class="sidebar-license" :class="{ 'is-active': activeView === 'accounts' }" @click="goToView('accounts')"><el-icon><User /></el-icon><span>账号中心</span></button>
        <button class="sidebar-license" :class="{ 'is-active': isGlobalSettingsView }" @click="goToView('global-settings')"><el-icon><Setting /></el-icon><span>系统设置</span><small>{{ licenseStatusLabel }}</small></button>
        <button class="sidebar-license" :disabled="exitRequested" @click="exitApplication"><el-icon><SwitchButton /></el-icon><span>{{ exitRequested ? '正在退出' : '安全退出' }}</span></button>
        <small v-if="appVersion" class="sidebar-version">v{{ appVersion }}</small>
      </div>
    </el-aside>
    <el-container class="workspace-shell-body">
      <el-header class="topbar">
        <div class="topbar-heading"><span class="topbar-kicker">{{ topbarKicker }}</span><h1>{{ viewTitle }}</h1><p>{{ viewSubtitle }}</p></div>
        <div class="topbar-actions"><button v-if="showEnvironmentStatus" class="topbar-env-tag" :class="topbarEnvClass" @click="goToView(isContentView ? 'content-settings' : isTrafficView ? 'traffic-settings' : 'settings')">{{ topbarEnvLabel }}</button><el-button :icon="Refresh" @click="refreshAll(true)">刷新</el-button></div>
      </el-header>
      <el-main class="main" :class="`view-${activeView}`">
        <div :class="{ 'workspace-page': isAcquisitionView || isMessageView }">
          <nav v-if="isAcquisitionView || isMessageView" class="workspace-tabs" :aria-label="isMessageView ? '客户工作区' : '获客工作区'">
            <button v-for="[name, label] in (isMessageView ? messageTabs : acquisitionTabs)" :key="name" type="button" :class="{ 'is-active': activeView === name }" :aria-current="activeView === name ? 'page' : undefined" @click="goToView(name)">{{ label }}</button>
          </nav>
          <RouterView v-slot="{ Component }">
            <!-- 保留详情和来源树的位置；创建表单、设置页仍正常卸载并执行离页保护。 -->
            <KeepAlive :include="['AiPage', 'OverviewPage', 'MessageWorkbenchPage', 'MessageInboxPage']" :max="4"><component :is="Component" v-bind="routeProps" v-on="routeListeners" /></KeepAlive>
          </RouterView>
        </div>
      </el-main>
    </el-container>
    <LicenseDialog :open="licenseDialogOpen" :loading="licenseLoading" :checking="licenseChecking" :info="licenseInfo" :code="licenseCodeDraft" @update:code="updateLicenseCodeDraft" @close="closeLicenseDialog" @save="saveLicenseCode" @check="checkLicense" @copy-device="copyDeviceCode" />
  </el-container>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import {
  Clock,
  House,
  User,
  Message,
  Monitor,
  Operation,
  Promotion,
  Refresh,
  Setting,
  SwitchButton,
  VideoPlay,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { api } from './shared/api'
import type { Dict } from './shared/types'
import { useAiWorkbench } from './composables/aiWorkbench'
import { createAutoSyncController, shouldAutoSyncView, shouldReplaceDraft, type AutoSyncReason } from './composables/autoSync'
import { useMessageWorkbench } from './composables/messageWorkbench'
import { useOverviewWorkbench } from './composables/overviewWorkbench'
import { useTableWorkbench } from './composables/tableWorkbench'
import { useTaskWorkbench } from './composables/taskWorkbench'
import { LicenseDialog } from './components/ui/LicenseDialog'

const router = useRouter()
const route = useRoute()

const settings = ref<Dict>({})
const settingsDraftDirty = ref(false)
const settingsSaving = ref(false)
const settingsSaveRevision = ref(0)
const env = ref<Dict>({})
const licenseInfo = ref<Dict>({})
const licenseDialogOpen = ref(false)
const licenseLoading = ref(false)
const licenseChecking = ref(false)
const licenseCodeDraft = ref('')
const licenseDraftDirty = ref(false)
const updateChecking = ref(false)
const exitRequested = ref(false)
const appVersion = ref('')
const appPackaged = ref(false)
const trafficEnv = ref<Dict>({})
const trafficRefreshSeq = ref(0)
const automationRefreshSeq = ref(0)
const runtimeRefreshSeq = ref(0)
const messageSettingsRefreshSeq = ref(0)
const messageInboxRefreshSeq = ref(0)
const globalSettingsRefreshSeq = ref(0)
const workspaceRefreshSeq = ref(0)
const contentEnv = ref<Dict>({})
const contentRefreshSeq = ref(0)
const workbenchStatus = ref<Dict>({ metrics: {}, active: false })
const tombstoneSummary = ref<Dict>({})
const tombstones = ref<Dict>({ items: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
const tombstoneFilters = ref<Dict>({ entity_type: '', platform: '', source: '', query: '', page: 1, page_size: 20 })
const sidebarMenu = ref<any>(null)
let settingsMutationSeq = 0
let licenseMutationSeq = 0

const {
  tasks,
  page: taskPage,
  pageSize: taskPageSize,
  total: taskTotal,
  query: taskQuery,
  selected: selectedTask,
  diagnostics: taskDiagnostics,
  dedupSummary: taskDedupSummary,
  retryDraft,
  loadTasks,
  loadDiagnostics: loadSelectedTaskDiagnostics,
  changePage: changeTaskPage,
  changeQuery: changeTaskQuery,
  refreshSelected: refreshSelectedTask,
  createTask,
  openLogs: openTaskLogs,
  selectTask,
  archiveTask,
  cancelTask,
  retryTask,
  consumeRetryDraft,
  deleteTask,
} = useTaskWorkbench({
  router,
  refreshRelated: () => Promise.allSettled([loadOverview(), loadAiJobs()]),
})

const {
  workbench: aiWorkbench,
  query: aiQuery,
  load: loadAiJobs,
  changeQuery: changeAiQuery,
  createJob: createAiJob,
  createBatchJobs: createBatchAiJobs,
  deleteNonCompetitors: deleteAiWorkbenchNonCompetitors,
  deleteNonCustomers: deleteAiWorkbenchNonCustomers,
  retryJob: retryAiJob,
  retryJobs: retryAiJobs,
} = useAiWorkbench({
  refreshRelated: () => Promise.allSettled([loadTable(activeLibrary.value), loadOverview()]),
})

const {
  tree: overviewTree,
  load: loadOverview,
  analyzeAccount: analyzeOverviewAccount,
  analyzeCustomerIntent: analyzeOverviewCustomerIntent,
  updateCustomerFollowStatus: updateOverviewCustomerFollowStatus,
  messageCustomer: messageOverviewCustomer,
  analyzeAccountCustomers: analyzeAccountCustomersIntent,
  analyzeKeywordCompetitors,
  deleteAccountNonCustomers,
  deleteAccount: deleteOverviewAccount,
  deleteCustomer: deleteOverviewCustomer,
  deleteKeywordNonCompetitors,
  deletePlatform: deleteOverviewPlatform,
  deleteKeyword: deleteOverviewKeyword,
  findCustomers,
} = useOverviewWorkbench({
  settings,
  refreshTasks: loadTasks,
  refreshAi: loadAiJobs,
  refreshTable: () => loadTable(activeLibrary.value),
  openTaskLogs,
})

const {
  library: activeLibrary,
  status: tableStatus,
  keyword: tableKeyword,
  rows: tableRows,
  loading: tableLoading,
  page: tablePage,
  pageSize: tablePageSize,
  total: tableTotal,
  load: loadTable,
  changeLibrary,
  changeFilter: changeTableFilter,
  changePage: changeTablePage,
  updateRow,
  deleteRow,
  analyzeRow: analyzeTableRow,
  enrichProfile,
} = useTableWorkbench({
  analyzeJob: createAiJob,
  refreshTasks: loadTasks,
  refreshOverview: loadOverview,
  openTaskLogs,
})

const {
  keywords: messageKeywords,
  customers: messageCustomers,
  detail: messageDetail,
  loading: messageLoading,
  filters: messageFilters,
  batches: messageBatches,
  load: loadMessageWorkbench,
  loadBatches: loadMessageBatches,
  changeFilter: changeMessageWorkbenchFilter,
  selectCustomer: selectMessageWorkbenchCustomer,
  closeDetail: closeMessageWorkbenchDetail,
  updateFollowStatus: updateMessageWorkbenchFollowStatus,
  messageCustomer: messageWorkbenchCustomer,
  confirmMessageCustomer: confirmMessageWorkbenchCustomer,
  autoMessageCustomer: autoMessageWorkbenchCustomer,
  startBatch: startMessageAutoBatch,
  cancelBatch: cancelMessageAutoBatch,
  retryBatch: retryMessageAutoBatch,
  deleteBatch: deleteMessageAutoBatch,
} = useMessageWorkbench({
  router,
  settings,
  refreshRelated: () => Promise.allSettled([loadOverview(), loadAiJobs(), loadTable(activeLibrary.value, true)]),
})

const activeView = computed(() => String(route.name || 'workspace'))
const acquisitionTabs = [['acquisition', '采集与结果'], ['ai', 'AI筛选'], ['overview', '来源与证据'], ['settings', '采集与筛选规则']]
const messageTabs = [['message-workbench', '客户跟进'], ['message-inbox', '私信收件箱'], ['message-batches', '触达记录'], ['message-settings', '私信设置']]
const isAcquisitionView = computed(() => acquisitionTabs.some(([name]) => name === activeView.value) || activeView.value === 'tables')
const isAutomationView = computed(() => activeView.value === 'automation-plans')
const isRuntimeView = computed(() => activeView.value === 'runtime-center')
const isMessageView = computed(() => activeView.value.startsWith('message-'))
const isGlobalSettingsView = computed(() => activeView.value === 'global-settings')
const isTrafficView = computed(() => activeView.value.startsWith('traffic-'))
const isContentView = computed(() => activeView.value.startsWith('content-'))
const activeMenuItem = computed(() => isAcquisitionView.value ? 'acquisition' : isMessageView.value ? 'message-workbench' : isTrafficView.value ? 'traffic-plans' : ({
  'content-settings': 'content-create',
} as Record<string, string>)[activeView.value] || activeView.value)
const activeMenuGroup = computed(() => isContentView.value ? 'content-workbench' : '')
const topbarKicker = computed(() => activeView.value === 'workspace' ? '日常工作' : activeView.value === 'accounts' ? '配置' : isGlobalSettingsView.value ? '系统中心' : (isRuntimeView.value ? '运行中心' : (isAutomationView.value ? '自动化中心' : (isMessageView.value ? '私信工作台' : (isContentView.value ? '内容工作台' : (isTrafficView.value ? '引流工作台' : '拓客工作台'))))))
const viewTitle = computed(() => isAcquisitionView.value ? '获客工作台' : isMessageView.value ? '客户工作台' : String(route.meta.title || '工作台'))
const viewSubtitle = computed(() => isAcquisitionView.value ? '从采集、筛选到来源证据，在同一工作区推进获客。' : isMessageView.value ? '跟进客户、收发私信、查看触达结果和调整私信规则。' : String(route.meta.subtitle || ''))
const envReady = computed(() => Boolean(env.value?.collector_component?.ok && env.value?.collector_storage?.ok))
const showEnvironmentStatus = computed(() => ['acquisition', 'settings', 'content-create', 'content-settings', 'traffic-plans', 'traffic-settings'].includes(activeView.value))
const topbarEnvOk = computed(() => isContentView.value ? Boolean(contentEnv.value?.ok) : isTrafficView.value ? Boolean(trafficEnv.value?.ok) : envReady.value)
const topbarEnvClass = computed(() => topbarEnvOk.value ? 'is-ok' : 'is-warn')
const topbarEnvLabel = computed(() => {
  if (isContentView.value) return topbarEnvOk.value ? '视频环境正常' : '需要检查视频环境'
  if (isTrafficView.value) return topbarEnvOk.value ? '引流环境正常' : '需要检查引流环境'
  return topbarEnvOk.value ? '环境就绪' : '需要检查环境'
})
const hasActiveAsyncWork = computed(() => {
  return Boolean(workbenchStatus.value?.active)
})
const autoSync = createAutoSyncController({
  sync: syncCurrentView,
  interval: () => hasActiveAsyncWork.value ? 3000 : 12000,
})
const licenseStatusLabel = computed(() => {
  if (licenseInfo.value.authorized) return '已授权'
  return licenseInfo.value.status === 'failed' ? '未授权' : '待授权'
})

const routeProps = computed(() => {
  if (activeView.value === 'acquisition') {
    return {
      tasks: tasks.value,
      page: taskPage.value,
      pageSize: taskPageSize.value,
      total: taskTotal.value,
      settings: settings.value,
      retryDraft: retryDraft.value || undefined,
      selectedTask: selectedTask.value || undefined,
      diagnostics: taskDiagnostics.value,
      dedupSummary: taskDedupSummary.value,
      query: taskQuery.value,
    }
  }
  if (activeView.value === 'overview') return { tree: overviewTree.value }
  if (activeView.value === 'ai') return { workbench: aiWorkbench.value, query: aiQuery.value }
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
  if (activeView.value === 'message-batches') return { batches: messageBatches.value }
  if (activeView.value === 'message-inbox') return { refreshSeq: messageInboxRefreshSeq.value }
  if (activeView.value === 'message-settings') {
    return {
      settings: settings.value,
      settingsSaveRevision: settingsSaveRevision.value,
      refreshSeq: messageSettingsRefreshSeq.value,
    }
  }
  if (activeView.value === 'tables') {
    return {
      library: activeLibrary.value,
      rows: tableRows.value,
      loading: tableLoading.value,
      page: tablePage.value,
      pageSize: tablePageSize.value,
      total: tableTotal.value,
      statusFilter: tableStatus.value,
      keywordFilter: tableKeyword.value,
    }
  }
  if (activeView.value.startsWith('traffic-')) return { refreshSeq: trafficRefreshSeq.value }
  if (activeView.value === 'automation-plans') return { refreshSeq: automationRefreshSeq.value }
  if (['global-settings', 'accounts'].includes(activeView.value)) {
    return {
      refreshSeq: globalSettingsRefreshSeq.value,
      licenseInfo: licenseInfo.value,
      appVersion: appVersion.value,
      appPackaged: appPackaged.value,
      updateChecking: updateChecking.value,
      tombstoneSummary: tombstoneSummary.value,
      tombstones: tombstones.value,
      tombstoneFilters: tombstoneFilters.value,
    }
  }
  if (activeView.value === 'workspace') return { refreshSeq: workspaceRefreshSeq.value }
  if (activeView.value === 'runtime-center') return { refreshSeq: runtimeRefreshSeq.value }
  if (activeView.value.startsWith('content-')) return { refreshSeq: contentRefreshSeq.value }
  if (activeView.value === 'settings') {
    return {
      settings: settings.value,
      settingsSaveRevision: settingsSaveRevision.value,
      env: env.value,
    }
  }
  return {}
})

const routeListeners = computed(() => {
  // 只把当前页面声明的事件传下去，避免 fragment 页面收到无关监听器 warning。
  if (activeView.value.startsWith('traffic-')) return { 'navigate-view': goToView }
  if (activeView.value === 'acquisition') {
    return {
      'create-task': createTask,
      'open-logs': openTaskLogs,
      'consume-retry-draft': consumeRetryDraft,
      'change-task-page': changeTaskPage,
      'select-task': selectTask,
      'retry-task': retryTask,
      'cancel-task': cancelTask,
      'archive-task': archiveTask,
      'delete-task': deleteTask,
      'change-task-query': changeTaskQuery,
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
      'query-change': changeAiQuery,
    }
  }
  if (activeView.value === 'message-workbench') {
    return {
      'filter-change': changeMessageWorkbenchFilter,
      'select-customer': selectMessageWorkbenchCustomer,
      'message-customer': messageWorkbenchCustomer,
      'confirm-message-customer': confirmMessageWorkbenchCustomer,
      'auto-message-customer': autoMessageWorkbenchCustomer,
      'start-auto-message-batch': startMessageAutoBatch,
      'cancel-auto-message-batch': cancelMessageAutoBatch,
      'update-follow-status': updateMessageWorkbenchFollowStatus,
      'close-detail': closeMessageWorkbenchDetail,
    }
  }
  if (activeView.value === 'message-batches') {
    return {
      'select-auto-message-batch': (batch: Dict) => loadMessageBatches(String(batch.id || ''), batch),
      'cancel-auto-message-batch': cancelMessageAutoBatch,
      'retry-auto-message-batch': retryMessageAutoBatch,
      'delete-auto-message-batch': deleteMessageAutoBatch,
    }
  }
  if (activeView.value === 'message-settings') {
    return {
      save: saveSettings,
      'settings-dirty-change': updateSettingsDraftDirty,
    }
  }
  if (activeView.value === 'tables') {
    return {
      'change-library': changeLibrary,
      'change-filter': changeTableFilter,
      'change-page': changeTablePage,
      'update-row': updateRow,
      'delete-row': deleteRow,
      'analyze-row': analyzeTableRow,
      'enrich-profile': enrichProfile,
      'find-customers': findCustomers,
    }
  }
  if (['global-settings', 'accounts'].includes(activeView.value)) {
    return {
      'open-license': openLicenseDialog,
      'check-update': checkForUpdates,
      'clear-data': clearAllData,
      'load-tombstones': loadTombstones,
    }
  }
  if (activeView.value === 'settings') {
    return {
      save: saveSettings,
      'settings-dirty-change': updateSettingsDraftDirty,
      'check-env': checkEnv,
    }
  }
  return {}
})

// 获客与客户页签保留完整工作位置；其它模块的侧栏仍只恢复列表筛选。
const listQueryFields: Record<string, string[]> = {
  'traffic-plans': ['archive', 'page'],
  'traffic-monitor': ['archive', 'page'],
  'traffic-records': ['query', 'platform', 'status', 'action', 'page', 'page_size'],
  'automation-plans': ['section', 'plan_type', 'plan_page', 'plan_page_size', 'run_page', 'run_page_size'],
  'runtime-center': ['status', 'kind', 'page', 'page_size'],
}
const rememberedViewQueries: Record<string, Dict> = {}
watch(() => [route.name, route.query] as const, ([name, query], previous) => {
  const view = String(name)
  const fields = listQueryFields[view]
  if ([...acquisitionTabs, ...messageTabs].some(([tab]) => tab === view) || view === 'tables') rememberedViewQueries[view] = { ...query }
  else if (fields?.some(field => field in query)) rememberedViewQueries[view] = Object.fromEntries(fields.filter(field => field in query).map(field => [field, query[field]]))
  if (view !== 'acquisition') return
  // 地址里的采集筛选同时支持刷新、后退和页签返回；同路由变化只重读列表。
  const page = Math.max(1, Number(query.page || 1))
  const pageSize = Math.max(1, Number(query.page_size || 10))
  const search = String(query.query || '')
  const changed = taskPage.value !== page || taskPageSize.value !== pageSize || taskQuery.value !== search
  taskPage.value = page; taskPageSize.value = pageSize; taskQuery.value = search
  if (!query.task) selectedTask.value = null
  if (changed && previous?.[0] === name) void loadTasks()
}, { immediate: true })
function goToView(view: string) {
  router.push({ name: view, query: rememberedViewQueries[view] || {} })
}

async function refreshAll(notifyFailure = false) {
  // 手动刷新只请求当前工作台，避免每次刷新拉取所有业务大列表。
  const statusLoaders = isGlobalSettingsView.value ? [] : [loadWorkbenchStatus()]
  const results = await Promise.allSettled([...statusLoaders, ...currentViewLoaders(true, true).map(loader => loader())])
  const failures = results.filter((result): result is PromiseRejectedResult => result.status === 'rejected')
  if (failures.length > 0) {
    if (notifyFailure) {
      const reason: any = failures[0].reason
      const detail = reason?.response?.data?.detail || reason?.message || '无法连接本地服务'
      ElMessage.error(`刷新失败（${failures.length} 项）：${detail}`)
    }
    return false
  }
  autoSync.markSynced()
  return true
}

async function loadLicense(silent = false) {
  const requestSeq = licenseMutationSeq
  try {
    const { data } = await api.get('/license')
    licenseInfo.value = data
    if (shouldReplaceDraft(licenseDraftDirty.value, licenseChecking.value, requestSeq, licenseMutationSeq)) {
      licenseCodeDraft.value = String(data.license_code || '')
    }
  } catch (error: any) {
    if (!silent) ElMessage.error(error?.response?.data?.detail || '授权信息加载失败')
  }
}

async function openLicenseDialog() {
  licenseDialogOpen.value = true
  licenseDraftDirty.value = false
  licenseMutationSeq += 1
  licenseLoading.value = true
  try {
    await loadLicense()
  } finally {
    licenseLoading.value = false
  }
}

function updateLicenseCodeDraft(value: string) {
  licenseCodeDraft.value = value
  licenseDraftDirty.value = true
  licenseMutationSeq += 1
}

function closeLicenseDialog() {
  licenseDialogOpen.value = false
  licenseDraftDirty.value = false
}

async function checkForUpdates() {
  // 打包版先正常关闭本地服务，再由稳定启动器执行签名更新流程。
  try {
    await ElMessageBox.confirm('检查更新需要关闭并重新启动应用；如果仍有排队或运行中的任务，系统会阻止重启。确认继续？', '检查更新', { type: 'warning' })
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    throw error
  }
  updateChecking.value = true
  try {
    const { data } = await api.post('/system/check-update')
    ElMessage.success(data.message || '应用正在重启并检查更新')
  } catch (error: any) {
    updateChecking.value = false
    ElMessage.error(error?.response?.data?.detail || '无法启动更新检查')
  }
}

async function exitApplication() {
  try {
    const taskNotice = workbenchStatus.value.active ? '当前仍有任务，退出时会安全停止或标记中断。' : '当前没有运行中的任务。'
    await ElMessageBox.confirm(`${taskNotice} 确认关闭应用和本地服务？`, '安全退出', { type: 'warning' })
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    throw error
  }
  exitRequested.value = true
  try {
    const { data } = await api.post('/system/exit')
    ElMessage.success(data.message || '应用正在安全退出')
    autoSync.stop()
    window.setTimeout(closeFrontendPage, 120)
  } catch (error: any) {
    exitRequested.value = false
    ElMessage.error(error?.response?.data?.detail || '无法安全退出应用')
  }
}

function closeFrontendPage() {
  // 浏览器禁止脚本关闭手动打开的标签页时，至少离开已停止服务的应用页面。
  window.location.replace('about:blank')
  window.close()
}

async function saveLicenseCode() {
  licenseChecking.value = true
  const saveSeq = ++licenseMutationSeq
  try {
    const { data } = await api.put('/license', { license_code: licenseCodeDraft.value })
    licenseInfo.value = data
    if (saveSeq === licenseMutationSeq) {
      licenseCodeDraft.value = String(data.license_code || '')
      licenseDraftDirty.value = false
    }
    ElMessage.success('授权码已保存')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '授权码保存失败')
  } finally {
    licenseChecking.value = false
  }
}

async function checkLicense() {
  licenseChecking.value = true
  const saveSeq = ++licenseMutationSeq
  try {
    const { data } = await api.post('/license/check', { license_code: licenseCodeDraft.value })
    licenseInfo.value = data
    if (saveSeq === licenseMutationSeq) {
      licenseCodeDraft.value = String(data.license_code || '')
      licenseDraftDirty.value = false
    }
    if (data.authorized) ElMessage.success(data.message || '授权校验通过')
    else ElMessage.error(data.message || '授权校验失败')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '授权校验失败')
  } finally {
    licenseChecking.value = false
  }
}

async function copyDeviceCode() {
  const deviceCode = String(licenseInfo.value.device_code || '').trim()
  if (!deviceCode) {
    ElMessage.warning('当前没有可复制的设备码')
    return
  }
  await navigator.clipboard.writeText(deviceCode)
  ElMessage.success('设备码已复制')
}

async function loadWorkbenchStatus() {
  // 应用壳只需要全局活跃状态；业务摘要由对应页面读取。
  const { data } = await api.get('/workbench/status', { params: { scope: 'runtime' } })
  workbenchStatus.value = data
}

async function loadContentShell() {
  const { data } = await api.get('/content/environment-check')
  contentEnv.value = data
}

async function loadSettings() {
  const requestSeq = settingsMutationSeq
  const { data } = await api.get('/settings')
  // 保存中或已有本地草稿时，忽略可能较旧的设置响应。
  if (requestSeq !== settingsMutationSeq) return
  if (['settings', 'message-settings'].includes(activeView.value)
    && !shouldReplaceDraft(settingsDraftDirty.value, settingsSaving.value, requestSeq, settingsMutationSeq)) return
  settings.value = data
}

async function checkEnv() {
  const { data } = await api.get('/settings/env-check')
  env.value = data
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

async function loadTrafficShell() {
  const { data } = await api.get('/traffic/environment-check')
  trafficEnv.value = data
}

function currentViewLoaders(includeStatic: boolean, refreshChild: boolean) {
  const loaders: Array<() => Promise<unknown>> = []
  if (!isTrafficView.value && !isContentView.value && !isAutomationView.value && !isRuntimeView.value && !isMessageView.value && !isGlobalSettingsView.value && !['workspace', 'accounts'].includes(activeView.value) && includeStatic) loaders.push(checkEnv)

  if (activeView.value === 'acquisition') {
    loaders.push(async () => { await loadTasks(); await refreshSelectedTask() })
    if (includeStatic) loaders.push(loadSettings)
  } else if (activeView.value === 'overview') {
    loaders.push(loadOverview)
  } else if (activeView.value === 'ai') {
    loaders.push(loadAiJobs)
  } else if (activeView.value === 'message-workbench') {
    loaders.push(() => loadMessageWorkbench(true))
    if (includeStatic) loaders.push(loadSettings)
  } else if (activeView.value === 'message-batches') {
    // 初次打开和自动刷新均服从地址中的批次及分页，避免覆盖页签恢复的位置。
    loaders.push(() => loadMessageBatches(String(route.query.batch || ''), { page: Number(route.query.page || 1), page_size: Number(route.query.page_size || 10), item_page: Number(route.query.item_page || 1), item_page_size: Number(route.query.item_page_size || 20) }))
  } else if (activeView.value === 'message-inbox') {
    // 收件箱自己刷新消息，应用壳只传递首次进入和手动刷新。
    if (includeStatic) loaders.push(async () => { messageInboxRefreshSeq.value += 1 })
  } else if (activeView.value === 'message-settings') {
    if (!settingsDraftDirty.value) loaders.push(loadSettings)
    if (includeStatic || refreshChild) loaders.push(async () => { messageSettingsRefreshSeq.value += 1 })
  } else if (activeView.value === 'tables') {
    loaders.push(() => loadTable(activeLibrary.value, true))
  } else if (activeView.value === 'settings') {
    if (!settingsDraftDirty.value) loaders.push(loadSettings)
  } else if (isTrafficView.value) {
    if (includeStatic) loaders.push(loadTrafficShell)
    if (refreshChild) loaders.push(async () => { trafficRefreshSeq.value += 1 })
  } else if (isContentView.value) {
    if (includeStatic) loaders.push(loadContentShell)
    if (refreshChild) loaders.push(async () => { contentRefreshSeq.value += 1 })
  } else if (isAutomationView.value) {
    if (includeStatic || refreshChild) loaders.push(async () => { automationRefreshSeq.value += 1 })
  } else if (isRuntimeView.value) {
    if (includeStatic || refreshChild) loaders.push(async () => { runtimeRefreshSeq.value += 1 })
  } else if (activeView.value === 'workspace') {
    if (includeStatic || refreshChild) loaders.push(async () => { workspaceRefreshSeq.value += 1 })
  } else if (activeView.value === 'accounts') {
    if (includeStatic || refreshChild) loaders.push(async () => { globalSettingsRefreshSeq.value += 1 })
  } else if (isGlobalSettingsView.value) {
    loaders.push(() => loadLicense(true))
    loaders.push(loadTombstoneSummary, () => loadTombstones())
    if (includeStatic || refreshChild) loaders.push(async () => { globalSettingsRefreshSeq.value += 1 })
  }
  return loaders
}

async function syncCurrentView(reason: AutoSyncReason) {
  const includeStatic = reason === 'route'
  const refreshView = shouldAutoSyncView(activeView.value, reason)
  const loaders = [
    ...(isGlobalSettingsView.value ? [] : [loadWorkbenchStatus]),
    ...(refreshView ? currentViewLoaders(includeStatic, !isContentView.value) : []),
  ]
  await Promise.allSettled(loaders.map(loader => loader()))
}

async function saveSettings(values: Dict, clearSecrets: string[] = []) {
  settingsSaving.value = true
  const saveSeq = ++settingsMutationSeq
  try {
    const { data } = await api.put('/settings', { values, ...(clearSecrets.length ? { clear_secrets: clearSecrets } : {}) })
    settings.value = data
    if (saveSeq === settingsMutationSeq) {
      settingsDraftDirty.value = false
      settingsSaveRevision.value += 1
    }
    ElMessage.success('设置已保存')
    await checkEnv()
  } finally {
    settingsSaving.value = false
  }
}

function updateSettingsDraftDirty(dirty: boolean) {
  settingsDraftDirty.value = dirty
  if (dirty) settingsMutationSeq += 1
}

async function clearAllData() {
  let value = ''
  try {
    const result = await ElMessageBox.prompt(
      '系统会先完整备份，再清空项目和原始采集库中的业务记录。配置、登录状态和本地素材文件保留。请输入“清空业务记录”确认。',
      '清空业务记录',
      {
        confirmButtonText: '清空',
        cancelButtonText: '取消',
        inputPlaceholder: '清空业务记录',
        inputPattern: /^清空业务记录$/,
        inputErrorMessage: '必须输入“清空业务记录”',
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
    ElMessage.success(`已清空数据：项目 ${data.project?.rows || 0} 行，底层 ${data.media_crawler?.rows || 0} 行；清空前备份已保留`)
    selectedTask.value = null
    await refreshAll(false)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '清空业务记录失败')
  }
}

watch(activeView, () => {
  void autoSync.trigger('route')
})

watch(activeMenuGroup, async group => {
  await nextTick()
  if (group) sidebarMenu.value?.open?.(group)
  else sidebarMenu.value?.close?.('content-workbench')
}, { immediate: true })

onMounted(async () => {
  // 版本在当前进程内不会变化，只需在页面启动时读取一次。
  const versionRequest = api.get('/health').then(({ data }) => {
    appVersion.value = String(data.version || '')
    appPackaged.value = Boolean(data.packaged)
  }).catch(() => undefined)
  await Promise.all([refreshAll(false), loadLicense(true), versionRequest])
  autoSync.start()
})

onBeforeUnmount(() => {
  autoSync.stop()
})
</script>
