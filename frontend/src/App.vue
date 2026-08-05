<template>
  <el-container class="shell">
    <el-aside class="sidebar" width="236px">
      <div class="brand">
        <img class="brand-mark" src="/ai-customer-icon.png" alt="" aria-hidden="true" />
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
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
import { useAiWorkbench } from './composables/aiWorkbench'
import { createAutoSyncController, type AutoSyncReason } from './composables/autoSync'
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
  changeBatchPage: changeMessageBatchPage,
  changeBatchItemPage: changeMessageBatchItemPage,
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
      page: taskPage.value,
      pageSize: taskPageSize.value,
      total: taskTotal.value,
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
      'change-task-page': changeTaskPage,
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
      'select-auto-message-batch': (batch: Dict) => loadMessageBatches(String(batch.id || ''), { item_page: 1 }),
      'change-auto-message-batch-page': changeMessageBatchPage,
      'change-auto-message-item-page': changeMessageBatchItemPage,
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
