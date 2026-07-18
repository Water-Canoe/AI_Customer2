<template>
  <el-container class="shell">
    <el-aside class="sidebar" width="236px">
      <div class="brand">
        <div class="brand-mark">AI</div>
        <div>
          <strong>AI获客系统</strong>
          <span>拓客 · 引流 · 内容 · 跟进</span>
        </div>
      </div>
      <el-menu ref="sidebarMenu" :default-active="activeView" :default-openeds="[]" :unique-opened="true" class="nav" @select="goToView">
        <el-menu-item index="automation-plans"><el-icon><Clock /></el-icon><span>自动化计划</span></el-menu-item>
        <el-menu-item index="runtime-center"><el-icon><Monitor /></el-icon><span>运行中心</span></el-menu-item>
        <el-sub-menu index="lead-workbench">
          <template #title><el-icon><Operation /></el-icon><span>拓客工作台</span></template>
          <el-menu-item index="tasks"><el-icon><Operation /></el-icon><span>任务管理</span></el-menu-item>
          <el-menu-item index="logs"><el-icon><Tickets /></el-icon><span>采集记录</span></el-menu-item>
          <el-menu-item index="overview"><el-icon><Share /></el-icon><span>总览树</span></el-menu-item>
          <el-menu-item index="ai"><el-icon><MagicStick /></el-icon><span>AI分析</span></el-menu-item>
          <el-menu-item index="tables"><el-icon><Grid /></el-icon><span>数据表</span></el-menu-item>
          <el-menu-item index="settings"><el-icon><Setting /></el-icon><span>拓客设置</span></el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="message-center">
          <template #title><el-icon><Message /></el-icon><span>私信工作台</span></template>
          <el-menu-item index="message-workbench"><el-icon><Message /></el-icon><span>客户跟进</span></el-menu-item>
          <el-menu-item index="message-batches"><el-icon><Tickets /></el-icon><span>批次记录</span></el-menu-item>
          <el-menu-item index="message-settings"><el-icon><Setting /></el-icon><span>私信设置</span></el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="traffic-workbench">
          <template #title><el-icon><Promotion /></el-icon><span>引流工作台</span></template>
          <el-menu-item index="traffic-plans"><el-icon><Promotion /></el-icon><span>计划工作台</span></el-menu-item>
          <el-menu-item index="traffic-monitor"><el-icon><Tickets /></el-icon><span>执行监控</span></el-menu-item>
          <el-menu-item index="traffic-records"><el-icon><Grid /></el-icon><span>操作记录</span></el-menu-item>
          <el-menu-item index="traffic-settings"><el-icon><Setting /></el-icon><span>引流设置</span></el-menu-item>
        </el-sub-menu>
        <el-sub-menu index="content-workbench">
          <template #title><el-icon><VideoPlay /></el-icon><span>内容工作台</span></template>
          <el-menu-item index="content-create"><el-icon><MagicStick /></el-icon><span>视频创作</span></el-menu-item>
          <el-menu-item index="content-assets"><el-icon><Collection /></el-icon><span>内容资产</span></el-menu-item>
          <el-menu-item index="content-records"><el-icon><Tickets /></el-icon><span>生成记录</span></el-menu-item>
          <el-menu-item index="content-publish"><el-icon><Promotion /></el-icon><span>发布中心</span></el-menu-item>
          <el-menu-item index="content-settings"><el-icon><Setting /></el-icon><span>内容设置</span></el-menu-item>
        </el-sub-menu>
      </el-menu>
      <div class="sidebar-footer">
        <button
          type="button"
          class="sidebar-license"
          :class="[licenseStatusClass, { 'is-active': isGlobalSettingsView }]"
          title="管理更新、授权设备和平台账号"
          aria-label="打开全局设置"
          @click="goToView('global-settings')"
        >
          <el-icon><Setting /></el-icon>
          <span class="sidebar-license-copy">
            <strong>全局设置</strong>
            <small>{{ licenseStatusLabel }}</small>
          </span>
          <i class="sidebar-license-dot" aria-hidden="true"></i>
        </button>
        <button
          type="button"
          class="sidebar-license"
          title="安全停止任务队列和本地服务，并关闭当前页面"
          aria-label="安全退出应用"
          :disabled="exitRequested"
          @click="exitApplication"
        >
          <el-icon><SwitchButton /></el-icon>
          <span class="sidebar-license-copy">
            <strong>安全退出</strong>
            <small>{{ exitRequested ? '正在安全退出' : '关闭页面与本地服务' }}</small>
          </span>
        </button>
        <small v-if="appVersion" class="sidebar-version">版本 v{{ appVersion }}</small>
      </div>
    </el-aside>

    <el-container>
      <el-header class="topbar">
        <div class="topbar-heading">
          <span class="topbar-kicker">{{ topbarKicker }}</span>
          <h1>{{ viewTitle }}</h1>
          <p>{{ viewSubtitle }}</p>
        </div>
        <div v-if="dashboardInsights.length" class="topbar-insights" aria-label="当前工作台指标">
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
          <span class="topbar-env-tag" :class="topbarEnvClass">{{ topbarEnvLabel }}</span>
          <el-button :icon="Refresh" @click="refreshAll(true)">刷新</el-button>
          <el-button v-if="!isGlobalSettingsView && !isRuntimeView && !isMessageView" type="primary" :icon="Plus" @click="createFromTopbar">{{ topbarPrimaryAction }}</el-button>
        </div>
      </el-header>

      <el-main class="main" :class="`view-${activeView}`">
        <RouterView v-slot="{ Component }">
          <component :is="Component" v-bind="routeProps" v-on="routeListeners" />
        </RouterView>
      </el-main>
    </el-container>
    <LicenseDialog
      :open="licenseDialogOpen"
      :loading="licenseLoading"
      :checking="licenseChecking"
      :info="licenseInfo"
      :code="licenseCodeDraft"
      @update:code="licenseCodeDraft = $event"
      @close="licenseDialogOpen = false"
      @save="saveLicenseCode"
      @check="checkLicense"
      @copy-device="copyDeviceCode"
    />
  </el-container>
</template>

<script setup lang="ts">
import { computed, h, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import {
  Collection,
  Clock,
  Grid,
  MagicStick,
  Message,
  Monitor,
  Operation,
  Plus,
  Promotion,
  Refresh,
  Setting,
  Share,
  SwitchButton,
  Tickets,
  VideoPlay,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { api } from './shared/api'
import type { Dict } from './shared/types'
import { competitorStatusLabel, platformName } from './shared/format'
import { createAutoSyncController, type AutoSyncReason } from './composables/autoSync'
import { selectDmScript, useMessageWorkbench } from './composables/messageWorkbench'
import { LicenseDialog } from './components/ui/LicenseDialog'

const router = useRouter()
const route = useRoute()

const activeLibrary = ref('contents')
const tableStatus = ref('')
const tableKeyword = ref('')
const tasks = ref<Dict[]>([])
const taskPage = ref(1)
const taskPageSize = ref(10)
const taskTotal = ref(0)
const taskTotalPages = ref(1)
const taskQuery = ref('')
const tableRows = ref<Dict[]>([])
const tableLoading = ref(false)
const tablePage = ref(1)
const tablePageSize = ref(20)
const tableTotal = ref(0)
const tableTotalPages = ref(1)
const overviewTree = ref<Dict[]>([])
const aiWorkbench = ref<Dict>({})
const aiQuery = ref<Dict>({ tab: 'competitors', keyword: '', status: '', result: '', page: 1, page_size: 10 })
const selectedTask = ref<Dict | null>(null)
const taskDiagnostics = ref<Dict>({})
const taskDedupSummary = ref<Dict>({})
const retryDraft = ref<Dict | null>(null)
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
const updateChecking = ref(false)
const exitRequested = ref(false)
const appVersion = ref('')
const appPackaged = ref(false)
const trafficEnv = ref<Dict>({})
const trafficRefreshSeq = ref(0)
const automationRefreshSeq = ref(0)
const runtimeRefreshSeq = ref(0)
const messageSettingsRefreshSeq = ref(0)
const globalSettingsRefreshSeq = ref(0)
const contentEnv = ref<Dict>({})
const contentRefreshSeq = ref(0)
const workbenchStatus = ref<Dict>({ metrics: {}, active: false })
const tombstoneSummary = ref<Dict>({})
const tombstones = ref<Dict>({ items: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
const tombstoneFilters = ref<Dict>({ entity_type: '', platform: '', source: '', query: '', page: 1, page_size: 20 })
const sidebarMenu = ref<any>(null)
let settingsMutationSeq = 0

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
  autoMessageCustomer: autoMessageWorkbenchCustomer,
  startBatch: startMessageAutoBatch,
  cancelBatch: cancelMessageAutoBatch,
  retryBatch: retryMessageAutoBatch,
  deleteBatch: deleteMessageAutoBatch,
} = useMessageWorkbench({
  settings,
  refreshRelated: () => Promise.allSettled([loadOverview(), loadAiJobs(), loadTable(activeLibrary.value, true)]),
})

const activeView = computed(() => String(route.name || 'tasks'))
const isAutomationView = computed(() => activeView.value === 'automation-plans')
const isRuntimeView = computed(() => activeView.value === 'runtime-center')
const isMessageView = computed(() => activeView.value.startsWith('message-'))
const isGlobalSettingsView = computed(() => activeView.value === 'global-settings')
const isTrafficView = computed(() => activeView.value.startsWith('traffic-'))
const isContentView = computed(() => activeView.value.startsWith('content-'))
const activeMenuGroup = computed(() => {
  if (isMessageView.value) return 'message-center'
  if (isTrafficView.value) return 'traffic-workbench'
  if (isContentView.value) return 'content-workbench'
  if (['tasks', 'logs', 'overview', 'ai', 'tables', 'settings'].includes(activeView.value)) return 'lead-workbench'
  return ''
})
const topbarKicker = computed(() => isGlobalSettingsView.value ? '系统中心' : (isRuntimeView.value ? '运行中心' : (isAutomationView.value ? '自动化中心' : (isMessageView.value ? '私信工作台' : (isContentView.value ? '内容工作台' : (isTrafficView.value ? '引流工作台' : '拓客工作台'))))))
const viewTitle = computed(() => String(route.meta.title || '任务管理'))
const viewSubtitle = computed(() => String(route.meta.subtitle || ''))
const envReady = computed(() => Boolean(env.value?.collector_component?.ok && env.value?.collector_storage?.ok))
const topbarEnvOk = computed(() => isAutomationView.value || isRuntimeView.value || isMessageView.value || isGlobalSettingsView.value || (isContentView.value ? Boolean(contentEnv.value?.ok) : (isTrafficView.value ? Boolean(trafficEnv.value?.ok) : envReady.value)))
const topbarEnvClass = computed(() => isAutomationView.value || isRuntimeView.value || isMessageView.value || isGlobalSettingsView.value ? 'is-neutral' : (topbarEnvOk.value ? 'is-ok' : 'is-warn'))
const topbarEnvLabel = computed(() => {
  if (isGlobalSettingsView.value) return licenseStatusLabel.value
  if (isRuntimeView.value) return '五类资源统一调度'
  if (isAutomationView.value) return '共用浏览器队列'
  if (isMessageView.value) return '私信账号独立登录态'
  if (isContentView.value) return topbarEnvOk.value ? '视频环境正常' : '需要检查视频环境'
  if (isTrafficView.value) return topbarEnvOk.value ? '引流环境正常' : '需要检查引流环境'
  return topbarEnvOk.value ? '环境就绪' : '需要检查环境'
})
const topbarPrimaryAction = computed(() => {
  if (isContentView.value) return '创作视频'
  if (isTrafficView.value || activeView.value === 'automation-plans') return '新建计划'
  return '新建任务'
})
const hasActiveAsyncWork = computed(() => {
  return Boolean(workbenchStatus.value?.active)
})
const autoSync = createAutoSyncController({
  sync: syncCurrentView,
  interval: () => hasActiveAsyncWork.value ? 3000 : 12000,
})
const dashboardInsights = computed(() => {
  const metrics = workbenchStatus.value?.metrics || {}
  if (isGlobalSettingsView.value) return []
  if (isMessageView.value) {
    return [
      { label: '待私信', value: compactCount(metrics.pending_customers), tone: 'amber' },
      { label: '待回复', value: compactCount(metrics.waiting_reply), tone: 'blue' },
      { label: '运行批次', value: compactCount(metrics.active_batches), tone: 'green' },
      { label: '失败批次', value: compactCount(metrics.failed_batches), tone: 'red' },
    ]
  }
  if (isRuntimeView.value) {
    return [
      { label: '排队中', value: compactCount(metrics.queued), tone: 'amber' },
      { label: '执行中', value: compactCount(metrics.running), tone: 'blue' },
      { label: '失败', value: compactCount(metrics.failed), tone: 'red' },
      { label: '被中断', value: compactCount(metrics.interrupted), tone: 'red' },
    ]
  }
  if (isAutomationView.value) {
    return [
      { label: '启用计划', value: compactCount(metrics.enabled_plans), tone: 'green' },
      { label: '运行中', value: compactCount(metrics.active_runs), tone: 'blue' },
      { label: '失败', value: compactCount(metrics.failed_runs), tone: 'red' },
    ]
  }
  if (isContentView.value) {
    return [
      { label: '内容资产', value: compactCount(metrics.assets), tone: 'green' },
      { label: '生成中', value: compactCount(metrics.active_jobs), tone: 'blue' },
      { label: '已完成', value: compactCount(metrics.succeeded_jobs), tone: 'green' },
      { label: '失败', value: compactCount(metrics.failed_jobs), tone: 'red' },
    ]
  }
  if (isTrafficView.value) {
    return [
      { label: '运行批次', value: compactCount(metrics.active_runs), tone: 'blue' },
      { label: '成功动作', value: compactCount(metrics.successful_actions), tone: 'green' },
      { label: '失败/跳过', value: compactCount(metrics.failed_actions), tone: 'red' },
    ]
  }
  return [
    { label: '运行任务', value: compactCount(metrics.active_tasks), tone: 'blue' },
    { label: 'AI待处理', value: compactCount(metrics.ai_pending), tone: 'amber' },
    { label: '目标客户', value: compactCount(metrics.target_customers), tone: 'green' },
    { label: '失败待查', value: compactCount(Number(metrics.failed_tasks || 0) + Number(metrics.ai_failed || 0)), tone: 'red' },
  ]
})
const licenseStatusLabel = computed(() => {
  if (licenseInfo.value.authorized) return '已授权'
  return licenseInfo.value.status === 'failed' ? '未授权' : '待授权'
})
const licenseStatusClass = computed(() => {
  if (licenseInfo.value.authorized) return 'is-authorized'
  return licenseInfo.value.status === 'failed' ? 'is-denied' : 'is-pending'
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
  if (activeView.value === 'ai') return { workbench: aiWorkbench.value }
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
  if (activeView.value === 'message-settings') {
    return {
      settings: settings.value,
      settingsSaveRevision: settingsSaveRevision.value,
      refreshSeq: messageSettingsRefreshSeq.value,
    }
  }
  if (activeView.value === 'logs') {
    return {
      tasks: tasks.value,
      selectedTask: selectedTask.value || undefined,
      diagnostics: taskDiagnostics.value,
      dedupSummary: taskDedupSummary.value,
      page: taskPage.value,
      pageSize: taskPageSize.value,
      total: taskTotal.value,
      totalPages: taskTotalPages.value,
      query: taskQuery.value,
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
      totalPages: tableTotalPages.value,
      statusFilter: tableStatus.value,
      keywordFilter: tableKeyword.value,
    }
  }
  if (activeView.value.startsWith('traffic-')) return { refreshSeq: trafficRefreshSeq.value }
  if (activeView.value === 'automation-plans') return { refreshSeq: automationRefreshSeq.value }
  if (activeView.value === 'global-settings') {
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
  if (activeView.value === 'runtime-center') return { refreshSeq: runtimeRefreshSeq.value }
  if (activeView.value.startsWith('content-')) return { refreshSeq: contentRefreshSeq.value }
  if (activeView.value === 'settings') {
    return {
      settings: settings.value,
      settingsSaveRevision: settingsSaveRevision.value,
      env: env.value,
    }
  }
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
      'query-change': changeAiQuery,
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
      'update-follow-status': updateMessageWorkbenchFollowStatus,
      'close-detail': closeMessageWorkbenchDetail,
    }
  }
  if (activeView.value === 'message-batches') {
    return {
      'select-auto-message-batch': (batch: Dict) => loadMessageBatches(String(batch.id || '')),
      'cancel-auto-message-batch': cancelMessageAutoBatch,
      'retry-auto-message-batch': retryMessageAutoBatch,
      'delete-auto-message-batch': deleteMessageAutoBatch,
    }
  }
  if (activeView.value === 'message-settings') {
    return {
      save: saveSettings,
      'settings-dirty-change': (dirty: boolean) => settingsDraftDirty.value = dirty,
    }
  }
  if (activeView.value === 'logs') {
    return {
      'select-task': selectTask,
      'retry-task': retryTask,
      'cancel-task': cancelTask,
      'archive-task': archiveTask,
      'delete-task': deleteTask,
      'change-task-page': changeTaskPage,
      'change-task-query': changeTaskQuery,
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
  if (activeView.value === 'global-settings') {
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
      'settings-dirty-change': (dirty: boolean) => settingsDraftDirty.value = dirty,
      'check-env': checkEnv,
    }
  }
  if (activeView.value.startsWith('traffic-')) return {}
  if (activeView.value.startsWith('content-')) return {}
  return {
    save: saveSettings,
    'settings-dirty-change': (dirty: boolean) => settingsDraftDirty.value = dirty,
    'check-env': checkEnv,
    'load-tombstones': loadTombstones,
    'clear-data': clearAllData,
  }
})

function goToView(view: string) {
  router.push({ name: view })
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
  try {
    const { data } = await api.get('/license')
    licenseInfo.value = data
    licenseCodeDraft.value = String(data.license_code || '')
  } catch (error: any) {
    if (!silent) ElMessage.error(error?.response?.data?.detail || '授权信息加载失败')
  }
}

async function openLicenseDialog() {
  licenseDialogOpen.value = true
  licenseLoading.value = true
  try {
    await loadLicense()
  } finally {
    licenseLoading.value = false
  }
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
  try {
    const { data } = await api.put('/license', { license_code: licenseCodeDraft.value })
    licenseInfo.value = data
    licenseCodeDraft.value = String(data.license_code || '')
    ElMessage.success('授权码已保存')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '授权码保存失败')
  } finally {
    licenseChecking.value = false
  }
}

async function checkLicense() {
  licenseChecking.value = true
  try {
    const { data } = await api.post('/license/check', { license_code: licenseCodeDraft.value })
    licenseInfo.value = data
    licenseCodeDraft.value = String(data.license_code || '')
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
  const scope = isRuntimeView.value ? 'runtime' : (isAutomationView.value ? 'automation' : (isMessageView.value ? 'message' : (isContentView.value ? 'content' : (isTrafficView.value ? 'traffic' : 'lead'))))
  const { data } = await api.get('/workbench/status', { params: { scope } })
  workbenchStatus.value = data
}

function createFromTopbar() {
  if (activeView.value === 'automation-plans') {
    router.push({ path: '/automation-plans', query: { create: '1' } })
    return
  }
  router.push(isContentView.value ? '/content-create' : (isTrafficView.value ? '/traffic-plans' : '/tasks'))
}

async function loadContentShell() {
  const { data } = await api.get('/content/environment-check')
  contentEnv.value = data
}

async function loadTasks() {
  const { data } = await api.get('/tasks', {
    params: { page: taskPage.value, page_size: taskPageSize.value, query: taskQuery.value }
  })
  tasks.value = data.items
  taskTotal.value = Number(data.total || 0)
  taskPage.value = Number(data.page || 1)
  taskPageSize.value = Number(data.page_size || taskPageSize.value)
  taskTotalPages.value = Number(data.total_pages || 1)
  if (!data.items.length && taskPage.value > taskTotalPages.value) {
    taskPage.value = taskTotalPages.value
    await loadTasks()
    return
  }
  if (!selectedTask.value && data.items.length) selectedTask.value = await fetchTask(data.items[0].id)
}

async function changeTaskPage(payload: Dict) {
  taskPage.value = Number(payload.page || 1)
  selectedTask.value = null
  taskDiagnostics.value = {}
  taskDedupSummary.value = {}
  await loadTasks()
  await loadSelectedTaskDiagnostics()
}

async function changeTaskQuery(query: string) {
  taskQuery.value = String(query || '').trim()
  taskPage.value = 1
  selectedTask.value = null
  taskDiagnostics.value = {}
  taskDedupSummary.value = {}
  await loadTasks()
  await loadSelectedTaskDiagnostics()
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
  const workbench = await api.get('/ai/workbench', { params: aiQuery.value })
  aiWorkbench.value = workbench.data
  if (!workbench.data.items?.length && Number(aiQuery.value.page || 1) > Number(workbench.data.total_pages || 1)) {
    aiQuery.value.page = Number(workbench.data.total_pages || 1)
    await loadAiJobs()
  }
}

async function changeAiQuery(query: Dict) {
  aiQuery.value = { ...aiQuery.value, ...query }
  await loadAiJobs()
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

async function loadTrafficShell() {
  const { data } = await api.get('/traffic/environment-check')
  trafficEnv.value = data
}

async function loadTable(library: string, silent = false) {
  if (!silent) tableLoading.value = true
  try {
    const { data } = await api.get(`/tables/${library}`, {
      params: {
        status: tableStatus.value,
        keyword: tableKeyword.value,
        page: tablePage.value,
        page_size: tablePageSize.value,
      }
    })
    tableRows.value = data.rows
    tableTotal.value = Number(data.total || 0)
    tablePage.value = Number(data.page || 1)
    tablePageSize.value = Number(data.page_size || tablePageSize.value)
    tableTotalPages.value = Number(data.total_pages || 1)
    if (!data.rows.length && tablePage.value > tableTotalPages.value) {
      tablePage.value = tableTotalPages.value
      await loadTable(library, silent)
    }
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

function compactCount(value: unknown) {
  const count = Number(value || 0)
  if (!Number.isFinite(count)) return '0'
  if (count >= 10000) return `${Math.round(count / 1000) / 10}万`
  if (count >= 1000) return `${Math.round(count / 100) / 10}k`
  return String(count)
}

function currentViewLoaders(includeStatic: boolean, refreshChild: boolean) {
  const loaders: Array<() => Promise<unknown>> = []
  if (!isTrafficView.value && !isContentView.value && !isAutomationView.value && !isRuntimeView.value && !isMessageView.value && !isGlobalSettingsView.value && includeStatic) loaders.push(checkEnv)

  if (activeView.value === 'tasks') {
    loaders.push(loadTasks)
    if (includeStatic) loaders.push(loadSettings)
  } else if (activeView.value === 'logs') {
    loaders.push(async () => { await loadTasks(); await refreshSelectedTask() })
  } else if (activeView.value === 'overview') {
    loaders.push(loadOverview)
  } else if (activeView.value === 'ai') {
    loaders.push(loadAiJobs)
  } else if (activeView.value === 'message-workbench') {
    loaders.push(() => loadMessageWorkbench(true))
    if (includeStatic) loaders.push(loadSettings)
  } else if (activeView.value === 'message-batches') {
    loaders.push(() => loadMessageBatches())
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
  } else if (isGlobalSettingsView.value) {
    loaders.push(() => loadLicense(true))
    loaders.push(loadTombstoneSummary, () => loadTombstones())
    if (includeStatic || refreshChild) loaders.push(async () => { globalSettingsRefreshSeq.value += 1 })
  }
  return loaders
}

async function syncCurrentView(reason: AutoSyncReason) {
  const includeStatic = reason === 'route'
  const loaders = [
    ...(isGlobalSettingsView.value ? [] : [loadWorkbenchStatus]),
    ...currentViewLoaders(includeStatic, !isContentView.value),
  ]
  await Promise.allSettled(loaders.map(loader => loader()))
}

async function changeLibrary(library: string) {
  activeLibrary.value = library
  tableStatus.value = ''
  tableKeyword.value = ''
  tablePage.value = 1
  await loadTable(library)
}

async function changeTableFilter(filters: Dict) {
  tableStatus.value = filters.status || ''
  tableKeyword.value = filters.keyword || ''
  tablePage.value = 1
  await loadTable(activeLibrary.value)
}

async function changeTablePage(payload: Dict) {
  tablePage.value = Number(payload.page || 1)
  tablePageSize.value = Number(payload.page_size || tablePageSize.value)
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
  await ElMessageBox.confirm('任务删除会同步清理项目库和原始采集映射。确认继续？', '硬删除确认', { type: 'warning' })
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
  return selectDmScript(settings.value, aiScript)
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
        note: `点击私信按钮：复制${scriptSelection.label}并打开客户主页`,
        record_message_attempt: true,
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
    await ElMessageBox.confirm('此操作会删除该账号相关内容、评论、线索来源和可清理账号，并同步清理原始采集映射。确认删除该账号？', '删除账号确认', { type: 'warning' })
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
    await ElMessageBox.confirm(`将硬删除“${platformName(platform)}”平台下的内容、评论、线索、账号来源，并同步清理原始采集映射。确认继续？`, '删除平台数据', { type: 'warning' })
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
    await ElMessageBox.confirm(`将硬删除“${platformName(platform)} / ${keyword}”关键词下的内容、评论、线索、账号来源，并同步清理原始采集映射。确认继续？`, '删除关键词数据', { type: 'warning' })
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
  for (const item of ['lead-workbench', 'message-center', 'traffic-workbench', 'content-workbench']) {
    if (item !== group) sidebarMenu.value?.close?.(item)
  }
  if (group) sidebarMenu.value?.open?.(group)
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
