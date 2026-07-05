import { computed, defineComponent, h, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  DataLine,
  Delete,
  Key,
  Monitor,
  Operation,
  Plus,
  Promotion,
  Refresh,
  Setting,
  Tickets,
  VideoPlay,
} from '@element-plus/icons-vue'

import { api } from '../shared/api'
import type { Dict } from '../shared/types'
import { SplitPane } from '../components/ui/SplitPane'
import { LicenseDialog } from '../components/ui/LicenseDialog'
import { emptyState, metricTile, sectionTitle } from '../components/ui/Workbench'

const defaultPlan = () => ({
  name: '',
  platform: 'dy',
  source_mode: 'random_feed',
  source_value: '',
  action_like: false,
  action_collect: false,
  action_follow: false,
  action_comment_text: false,
  action_comment_image: false,
  enabled: true,
})

// 来源模式取代旧的“定向/随机”独立页面，统一由计划工作台配置。
const sourceOptions = [
  ['random_feed', '随机推荐流'],
  ['competitor_videos', '拓客竞品视频'],
  ['collected_keyword', '已采集关键词'],
  ['search_keyword', '手动搜索关键词'],
]

export default defineComponent({
  name: 'TrafficWorkbenchPage',
  setup() {
    const route = useRoute()
    const plans = ref<Dict[]>([])
    const runs = ref<Dict[]>([])
    const selectedRun = ref<Dict | null>(null)
    const records = ref<Dict>({ rows: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
    const settings = ref<Dict>({ values: {}, texts: [], images: [] })
    const licenseInfo = ref<Dict>({})
    const licenseOpen = ref(false)
    const licenseLoading = ref(false)
    const licenseChecking = ref(false)
    const licenseCode = ref('')
    const keywords = ref<Dict[]>([])
    const videos = ref<Dict[]>([])
    const planDraft = ref<Dict>(defaultPlan())
    const settingsDraft = ref<Dict>({})
    const textDraft = ref<Dict[]>([])
    const imageDraft = ref<Dict[]>([])
    const uploadedImages = ref<Dict[]>([])
    const imageUploading = ref(false)
    const trafficEnv = ref<Dict>({})
    const envInstalling = ref(false)
    const envInstallResult = ref<Dict | null>(null)
    const douyinLoginOpening = ref(false)
    const planArchiveFilter = ref('active')
    const runArchiveFilter = ref('active')
    const recordFilters = ref({ query: '', platform: '', status: '', action: '', page: 1, page_size: 20 })
    const loading = ref(false)
    const trafficLogRef = ref<HTMLElement | null>(null)
    const trafficLogAutoFollow = ref(true)
    const LOG_BOTTOM_THRESHOLD = 28

    const view = computed(() => String(route.name || 'traffic-plans'))
    // 归档数据默认隐藏，只有用户切换到“已归档”时才展示。
    const visiblePlans = computed(() => plans.value.filter(plan => planArchiveFilter.value === 'archived' ? plan.archived : !plan.archived))
    const visibleRuns = computed(() => runs.value.filter(run => runArchiveFilter.value === 'archived' ? run.archived : !run.archived))

    onMounted(loadPage)
    watch(view, () => loadPage())
    watch(
      () => ({
        runId: String(selectedRun.value?.id || ''),
        logCount: (selectedRun.value?.logs || []).length,
      }),
      (current, previous) => {
        if (!current.runId) return
        const runChanged = !previous || current.runId !== previous.runId
        if (runChanged) {
          trafficLogAutoFollow.value = true
          scrollTrafficLogToBottom(true)
          return
        }
        if (!previous || current.logCount > previous.logCount) scrollTrafficLogToBottom(false)
      },
      { immediate: true },
    )

    async function loadPage() {
      // 四个子页面按需拉取数据，避免进入引流工作台时全量请求。
      if (view.value === 'traffic-plans') await Promise.all([loadPlans(), loadSources()])
      else if (view.value === 'traffic-monitor') await Promise.all([loadRuns()])
      else if (view.value === 'traffic-records') await loadRecords()
      else if (view.value === 'traffic-settings') await Promise.all([loadSettings(), loadTrafficLicense(), loadTrafficEnvironment()])
    }

    async function loadPlans() {
      const { data } = await api.get('/traffic/plans', { params: { include_archived: planArchiveFilter.value === 'archived' } })
      plans.value = data
    }

    async function loadRuns() {
      const { data } = await api.get('/traffic/runs', { params: { include_archived: runArchiveFilter.value === 'archived' } })
      runs.value = data
      if (selectedRun.value && !visibleRuns.value.some(run => run.id === selectedRun.value?.id)) selectedRun.value = null
      const firstVisible = visibleRuns.value[0]
      if (!selectedRun.value && firstVisible) await selectRun(firstVisible.id)
      if (selectedRun.value) await selectRun(selectedRun.value.id)
    }

    async function selectRun(id: string) {
      const { data } = await api.get(`/traffic/runs/${id}`)
      selectedRun.value = data
    }

    async function loadRecords() {
      const { data } = await api.get('/traffic/records', { params: recordFilters.value })
      records.value = data
    }

    async function loadSettings() {
      const { data } = await api.get('/traffic/settings')
      settings.value = data
      uploadedImages.value = []
      settingsDraft.value = { ...(data.values || {}) }
      textDraft.value = (data.texts || []).map((item: Dict) => ({ text: String(item.text || ''), enabled: materialEnabled(item.enabled), used_count: item.used_count || 0 }))
      imageDraft.value = (data.images || []).map((item: Dict) => ({
        path: String(item.path || ''),
        preview_url: String(item.preview_url || ''),
        enabled: materialEnabled(item.enabled),
        used_count: item.used_count || 0,
      }))
    }

    async function loadTrafficLicense() {
      const { data } = await api.get('/traffic/license')
      licenseInfo.value = data
      licenseCode.value = String(data.license_code || '')
    }

    async function loadTrafficEnvironment() {
      const { data } = await api.get('/traffic/environment-check')
      trafficEnv.value = data
    }

    async function loadSources() {
      const [keywordResult, videoResult] = await Promise.allSettled([
        api.get('/traffic/source-keywords'),
        api.get('/traffic/source-competitor-videos'),
      ])
      keywords.value = keywordResult.status === 'fulfilled' ? keywordResult.value.data : []
      videos.value = videoResult.status === 'fulfilled' ? videoResult.value.data : []
    }

    function choosePlatform(platform: string) {
      if (platform !== 'dy') {
        ElMessage.info('正在开发')
        return
      }
      planDraft.value.platform = platform
    }

    async function createPlan(startNow = false) {
      loading.value = true
      try {
        const { data } = await api.post('/traffic/plans', planDraft.value)
        ElMessage.success('引流计划已创建')
        planDraft.value = defaultPlan()
        await loadPlans()
        if (startNow) await startRun(data.id)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '计划创建失败')
      } finally {
        loading.value = false
      }
    }

    async function startRun(planId: string) {
      try {
        const { data } = await api.post(`/traffic/plans/${planId}/runs`)
        ElMessage.success('引流批次已启动')
        selectedRun.value = data
        await loadRuns()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '批次启动失败')
      }
    }

    async function deletePlan(planId: string) {
      try {
        await ElMessageBox.confirm('硬删除会同步删除该计划下的批次、日志、视频明细、操作记录和防重复账本。确认继续？', '删除引流计划', { type: 'warning' })
        await api.delete(`/traffic/plans/${planId}`)
        ElMessage.success('计划已删除')
        await Promise.all([loadPlans(), loadRuns(), loadRecords()])
      } catch (error: any) {
        if (isUserCancel(error)) return
        ElMessage.error(error?.response?.data?.detail || '计划删除失败')
      }
    }

    async function archivePlan(planId: string) {
      try {
        await api.post(`/traffic/plans/${planId}/archive`)
        ElMessage.success('计划已归档')
        await loadPlans()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '计划归档失败')
      }
    }

    async function restorePlan(planId: string) {
      try {
        await api.post(`/traffic/plans/${planId}/restore`)
        ElMessage.success('计划已恢复')
        await loadPlans()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '计划恢复失败')
      }
    }

    async function stopRun(runId: string) {
      await api.post(`/traffic/runs/${runId}/stop`)
      ElMessage.success('已发送停止请求')
      await loadRuns()
    }

    async function archiveRun(runId: string) {
      try {
        await api.post(`/traffic/runs/${runId}/archive`)
        ElMessage.success('批次已归档')
        await loadRuns()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '批次归档失败')
      }
    }

    async function restoreRun(runId: string) {
      try {
        await api.post(`/traffic/runs/${runId}/restore`)
        ElMessage.success('批次已恢复')
        await loadRuns()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '批次恢复失败')
      }
    }

    async function deleteRun(runId: string) {
      try {
        await ElMessageBox.confirm('硬删除会同步删除该批次的日志、视频明细、操作记录和防重复账本。确认继续？', '删除引流批次', { type: 'warning' })
        await api.delete(`/traffic/runs/${runId}`)
        ElMessage.success('批次已删除')
        if (selectedRun.value?.id === runId) selectedRun.value = null
        await Promise.all([loadRuns(), loadRecords()])
      } catch (error: any) {
        if (isUserCancel(error)) return
        ElMessage.error(error?.response?.data?.detail || '批次删除失败')
      }
    }

    async function saveSettings() {
      const texts = textDraft.value
        .map(item => ({ text: String(item.text || '').trim(), enabled: Boolean(item.enabled) }))
        .filter(item => item.text)
      const images = imageLines().map(item => ({ path: item.path, enabled: Boolean(item.enabled) }))
      const { data } = await api.put('/traffic/settings', { values: settingsDraft.value, texts, images })
      settings.value = data
      uploadedImages.value = []
      ElMessage.success('引流设置已保存')
    }

    async function uploadImages(event: Event) {
      const input = event.target as HTMLInputElement
      const files = Array.from(input.files || [])
      if (!files.length) return
      imageUploading.value = true
      try {
        for (const file of files) {
          if (!file.type.startsWith('image/')) {
            ElMessage.warning(`${file.name} 不是图片`)
            continue
          }
          const { data } = await api.post('/traffic/material-images', file, {
            params: { filename: file.name },
            headers: { 'Content-Type': 'application/octet-stream' },
          })
          appendImagePath(data)
          uploadedImages.value = [data, ...uploadedImages.value.filter(item => item.path !== data.path)]
        }
        ElMessage.success('图片已上传，请保存设置')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '图片上传失败')
      } finally {
        imageUploading.value = false
        input.value = ''
      }
    }

    async function installTrafficEnvironment() {
      envInstalling.value = true
      envInstallResult.value = null
      try {
        const { data } = await api.post('/traffic/environment-install')
        envInstallResult.value = data
        trafficEnv.value = data.check || {}
        if (data.ok) ElMessage.success('引流环境依赖已安装')
        else ElMessage.error('依赖安装失败，请查看输出')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '依赖安装失败')
      } finally {
        envInstalling.value = false
      }
    }

    async function openDouyinLogin() {
      douyinLoginOpening.value = true
      try {
        const { data } = await api.post('/traffic/douyin-login')
        ElMessage.success(data.message || '抖音登录窗口已打开')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '抖音登录窗口打开失败')
      } finally {
        douyinLoginOpening.value = false
      }
    }

    async function clearRecords() {
      await ElMessageBox.confirm('会清除引流批次、日志、操作记录和防重复账本，文案/图片/授权不会删除。', '清除引流记录', { type: 'warning' })
      await api.delete('/traffic/records')
      ElMessage.success('引流记录已清除')
      await Promise.all([loadRecords(), loadRuns()])
    }

    async function openLicense() {
      licenseOpen.value = true
      licenseLoading.value = true
      try {
        await loadTrafficLicense()
      } finally {
        licenseLoading.value = false
      }
    }

    async function saveLicense() {
      licenseChecking.value = true
      try {
        const { data } = await api.put('/traffic/license', { license_code: licenseCode.value })
        licenseInfo.value = data
        licenseCode.value = String(data.license_code || '')
        ElMessage.success('授权码已保存')
      } finally {
        licenseChecking.value = false
      }
    }

    async function checkLicense() {
      licenseChecking.value = true
      try {
        const { data } = await api.post('/traffic/license/check', { license_code: licenseCode.value })
        licenseInfo.value = data
        licenseCode.value = String(data.license_code || '')
        if (data.authorized) ElMessage.success(data.message || '授权校验通过')
        else ElMessage.error(data.message || '授权校验失败')
      } finally {
        licenseChecking.value = false
      }
    }

    function renderPlanPage() {
      return h(SplitPane, { class: 'traffic-card-split', storageKey: 'traffic-plans', side: 'right', defaultSideWidth: 620, minSideWidth: 420 }, {
        default: () => h('section', { class: 'pane content-pane traffic-split-main' }, [
          sectionTitle({ title: '创建引流计划', subtitle: '不选动作时就是纯自动刷视频', icon: Promotion, tone: 'teal' }),
          renderPlatformTabs(),
          h('div', { class: 'form-grid traffic-plan-form' }, [
            labelInput('计划名称', planDraft.value.name, value => planDraft.value.name = value, 'field-wide', `留空自动生成：${sourceLabel(planDraft.value.source_mode)}-计划ID`),
            labelSelect('来源模式', planDraft.value.source_mode, sourceOptions, value => planDraft.value.source_mode = value),
            labelInput(sourceValueLabel(), planDraft.value.source_value, value => planDraft.value.source_value = value, 'field-wide'),
            h('label', { class: 'form-field field-full' }, [
              h('span', '动作组合'),
              // 不选择任何动作时代表纯浏览，后端仍会记录已浏览视频。
              h('div', { class: 'toggles' }, [
                actionToggle('action_like', '点赞视频'),
                actionToggle('action_collect', '收藏视频'),
                actionToggle('action_follow', '关注作者'),
                actionToggle('action_comment_text', '评论文案'),
                actionToggle('action_comment_image', '评论图片'),
              ]),
            ]),
          ]),
          h('div', { class: 'bulk-preview-warning' }, [
            h('li', planActions(planDraft.value).length ? `将执行：${planActionLabel(planDraft.value)}` : '未选择动作：只自动刷视频并记录，不点赞、不收藏、不关注、不评论。'),
          ]),
          renderSourceShortcuts(),
          h('div', { class: 'task-card-actions traffic-form-actions' }, [
            h('button', { class: 'primary-soft', disabled: loading.value, onClick: () => createPlan(false) }, '保存计划'),
            h('button', { class: 'primary-action', disabled: loading.value, onClick: () => createPlan(true) }, '保存并启动'),
          ]),
        ]),
        side: () => h('aside', { class: 'pane side-pane traffic-split-side' }, [
          sectionTitle({ title: '计划列表', subtitle: `${visiblePlans.value.length} 个计划`, icon: Tickets, tone: 'blue' }),
          renderArchiveTabs(planArchiveFilter.value, async value => { planArchiveFilter.value = value; await loadPlans() }),
          renderPlanTable(),
        ]),
      })
    }

    function renderMonitorPage() {
      const run = selectedRun.value
      return h(SplitPane, { class: 'traffic-card-split', storageKey: 'traffic-monitor', side: 'right', defaultSideWidth: 620, minSideWidth: 420 }, {
        default: () => h('section', { class: 'pane content-pane traffic-split-main traffic-monitor-pane' }, [
          sectionTitle({ title: '执行详情', subtitle: run?.plan_name || '选择右侧批次', icon: Monitor, tone: 'blue' }),
          run ? [
            h('div', { class: 'ai-summary-grid' }, [
              metricTile({ label: '浏览视频', value: String(run.browsed_count || 0), tone: 'blue', icon: VideoPlay }),
              metricTile({ label: '成功动作', value: String(run.action_success_count || 0), tone: 'green', icon: Operation }),
              metricTile({ label: '失败/跳过', value: String((run.failed_count || 0) + (run.skipped_count || 0)), tone: 'red', icon: DataLine }),
            ]),
            run.stop_reason ? h('div', { class: 'bulk-preview-warning' }, [
              h('li', run.stop_reason),
              run.stop_suggestion ? h('li', run.stop_suggestion) : null,
            ]) : null,
            h('div', { class: 'traffic-log-list', ref: trafficLogRef, onScroll: updateTrafficLogFollowState }, (run.logs || []).length ? run.logs.map(renderLogLine) : [h('p', '暂无日志')]),
            sectionTitle({ title: '已处理视频', subtitle: `${(run.records || []).length} 条`, icon: VideoPlay, tone: 'green', compact: true }),
            h('div', { class: 'table-scroll traffic-detail-table' }, renderRecordsTable(run.records || [], '暂无视频记录')),
          ] : emptyState({ title: '请选择批次', description: '右侧选择一个批次查看日志', icon: Monitor }),
        ]),
        side: () => h('aside', { class: 'pane side-pane traffic-split-side' }, [
          sectionTitle({ title: '批次列表', subtitle: '运行状态与历史', icon: Tickets, tone: 'blue' }),
          h('div', { class: 'traffic-list-toolbar' }, [
            renderArchiveTabs(runArchiveFilter.value, async value => { runArchiveFilter.value = value; await loadRuns() }),
            h('button', { class: 'secondary-action', onClick: loadRuns }, [h(Refresh, { class: 'inline-icon' }), '刷新']),
          ]),
          renderRunTable(),
        ]),
      })
    }

    function renderRecordsPage() {
      const page = records.value.page || 1
      const totalPages = records.value.total_pages || 1
      return h('section', { class: 'pane table-workspace traffic-records-workspace' }, [
        h('div', { class: 'table-library-bar traffic-record-bar' }, [
          sectionTitle({ title: '操作记录', subtitle: `共 ${records.value.total || 0} 条`, icon: DataLine, tone: 'green' }),
          h('div', { class: 'table-filters traffic-record-filters' }, [
            h('input', { placeholder: '搜索视频/作者/评论', value: recordFilters.value.query, onInput: (event: Event) => recordFilters.value.query = (event.target as HTMLInputElement).value }),
            h('select', { value: recordFilters.value.platform, onChange: (event: Event) => { recordFilters.value.platform = (event.target as HTMLSelectElement).value; recordFilters.value.page = 1 } }, [
              h('option', { value: '' }, '全部平台'),
              h('option', { value: 'dy' }, '抖音'),
              h('option', { value: 'xhs' }, '小红书'),
              h('option', { value: 'ks' }, '快手'),
            ]),
            h('select', { value: recordFilters.value.status, onChange: (event: Event) => { recordFilters.value.status = (event.target as HTMLSelectElement).value; recordFilters.value.page = 1 } }, [
              h('option', { value: '' }, '全部状态'),
              h('option', { value: 'browsed' }, '仅浏览'),
              h('option', { value: 'done' }, '已执行'),
              h('option', { value: 'skipped' }, '已跳过'),
              h('option', { value: 'failed' }, '失败'),
            ]),
            h('select', { value: recordFilters.value.action, onChange: (event: Event) => { recordFilters.value.action = (event.target as HTMLSelectElement).value; recordFilters.value.page = 1 } }, [
              h('option', { value: '' }, '全部动作'),
              h('option', { value: 'like' }, '点赞'),
              h('option', { value: 'collect' }, '收藏'),
              h('option', { value: 'follow' }, '关注'),
              h('option', { value: 'comment_text' }, '评论文案'),
              h('option', { value: 'comment_image' }, '评论图片'),
            ]),
            h('button', { class: 'filter-button', onClick: () => { recordFilters.value.page = 1; loadRecords() } }, '筛选'),
          ]),
        ]),
        h('div', { class: 'table-content' }, [
          sectionTitle({ title: '记录表', subtitle: `${records.value.rows?.length || 0} / ${records.value.total || 0} 条`, icon: DataLine, tone: 'teal', compact: true }),
          h('div', { class: 'table-scroll' }, renderRecordsTable(records.value.rows || [], '暂无操作记录')),
          h('div', { class: 'table-pagination' }, [
            h('div', { class: 'table-page-size' }, [
              h('span', '每页'),
              h('select', { value: String(recordFilters.value.page_size), onChange: (event: Event) => { recordFilters.value.page_size = Number((event.target as HTMLSelectElement).value); recordFilters.value.page = 1; loadRecords() } }, [
                h('option', { value: '10' }, '10'),
                h('option', { value: '20' }, '20'),
                h('option', { value: '50' }, '50'),
              ]),
              h('span', '条'),
            ]),
            h('div', { class: 'table-page-controls' }, [
              h('button', { disabled: page <= 1, onClick: () => { recordFilters.value.page -= 1; loadRecords() } }, '上一页'),
              h('span', `${page} / ${totalPages}`),
              h('button', { disabled: page >= totalPages, onClick: () => { recordFilters.value.page += 1; loadRecords() } }, '下一页'),
            ]),
          ]),
        ]),
      ])
    }

    function renderSettingsPage() {
      return h(SplitPane, { class: 'traffic-card-split', storageKey: 'traffic-settings', side: 'right', defaultSideWidth: 320, minSideWidth: 280, maxSideWidth: 420 }, {
        default: () => h('section', { class: 'pane content-pane traffic-settings-pane traffic-split-main' }, [
          sectionTitle({ title: '引流设置', subtitle: '授权、文案、图片、限额统一在这里维护', icon: Setting, tone: 'teal' }),
          h('div', { class: 'task-card-actions traffic-settings-actions' }, [
            h('button', { class: 'secondary-action', onClick: openLicense }, [h(Key, { class: 'inline-icon' }), '授权与设备']),
            h('button', { class: 'primary-action', onClick: saveSettings }, '保存设置'),
          ]),
          h('div', { class: 'form-grid traffic-settings-form' }, [
            settingInput('traffic_round_video_limit', '每轮视频上限'),
            settingInput('traffic_daily_action_limit', '每日动作上限'),
            settingInput('traffic_action_probability', '操作执行概率%'),
            settingInput('traffic_min_watch_seconds', '最短停留秒数'),
            settingInput('traffic_max_watch_seconds', '最长停留秒数'),
            settingInput('traffic_author_cooldown_hours', '作者冷却小时'),
            settingInput('traffic_stop_after_failures', '连续失败停机次数'),
            h('div', { class: 'toggles field-full' }, [
              settingToggle('traffic_close_browser_on_failure', '失败后关闭浏览器'),
            ]),
            renderTextManager(),
            renderImageManager(),
          ]),
          renderLicenseDialog(),
        ]),
        side: () => h('aside', { class: 'pane side-pane traffic-settings-side traffic-split-side' }, [
          sectionTitle({ title: '授权状态', subtitle: licenseInfo.value.message || '尚未读取', icon: Key, tone: licenseInfo.value.authorized ? 'green' : 'amber' }),
          h('div', { class: ['license-status-card', licenseInfo.value.authorized ? 'authorized' : ''] }, [
            h('strong', licenseInfo.value.authorized ? '授权通过' : '未授权'),
            h('span', licenseInfo.value.message || '请填写引流授权码'),
            h('small', `设备码：${licenseInfo.value.device_code || '-'}`),
          ]),
          sectionTitle({ title: '抖音登录态', subtitle: '扫码后用于引流执行', icon: VideoPlay, tone: 'blue', compact: true }),
          h('p', { class: 'traffic-env-suggestion' }, '扫码后点开任意视频，关闭登录窗口，再重新启动引流批次。'),
          h('div', { class: 'task-card-actions traffic-login-actions' }, [
            h('button', { class: 'primary-action', disabled: douyinLoginOpening.value, onClick: openDouyinLogin }, douyinLoginOpening.value ? '打开中...' : '打开抖音登录窗口'),
          ]),
          renderTrafficEnvironment(),
          sectionTitle({ title: '危险操作', subtitle: '不可恢复', icon: Delete, tone: 'red', compact: true }),
          h('button', { class: 'text-icon-button danger', onClick: clearRecords }, [h(Delete, { class: 'inline-icon' }), '清除引流记录']),
        ]),
      })
    }

    function renderPlatformTabs() {
      return h('div', { class: 'library-list traffic-platform-tabs' }, [
        h('button', { class: { selected: planDraft.value.platform === 'dy' }, onClick: () => choosePlatform('dy') }, '抖音'),
        h('button', { onClick: () => choosePlatform('ks') }, '快手'),
        h('button', { onClick: () => choosePlatform('xhs') }, '小红书'),
      ])
    }

    function renderSourceShortcuts() {
      if (planDraft.value.source_mode === 'collected_keyword') {
        return h('div', { class: 'table-scroll traffic-source-shortcuts' }, [
          h('table', { class: 'data-table resizable-table' }, [
            h('thead', [h('tr', ['关键词', '视频数', '操作'].map(text => h('th', text)))]),
            h('tbody', keywords.value.slice(0, 20).length ? keywords.value.slice(0, 20).map(item => h('tr', [
              h('td', tableText(item.keyword || '-')),
              h('td', String(item.content_count || 0)),
              h('td', h('button', { class: 'text-icon-button reserved', onClick: () => planDraft.value.source_value = item.keyword }, '填入')),
            ])) : [emptyTableRow(3, '暂无已采集关键词')]),
          ]),
        ])
      }
      if (planDraft.value.source_mode === 'competitor_videos') {
        return h('div', { class: 'table-scroll traffic-source-shortcuts' }, [
          h('table', { class: 'data-table resizable-table' }, [
            h('thead', [h('tr', ['视频', '作者', '操作'].map(text => h('th', text)))]),
            h('tbody', videos.value.slice(0, 8).length ? videos.value.slice(0, 8).map(item => h('tr', [
              h('td', tableText(item.title || item.description || item.content_id)),
              h('td', tableText(item.author_name || '未知作者')),
              h('td', h('button', { class: 'text-icon-button reserved', onClick: () => planDraft.value.source_value = item.content_url || item.content_id }, '填入')),
            ])) : [emptyTableRow(3, '暂无竞品视频')]),
          ]),
        ])
      }
      return null
    }

    function renderPlanTable() {
      return h('div', { class: 'table-scroll traffic-side-scroll' }, [
        h('table', { class: 'data-table resizable-table traffic-side-table' }, [
          h('thead', [h('tr', ['计划名称', '平台', '来源模式', '来源内容', '动作组合', '状态', '创建时间', '操作'].map(text => h('th', text)))]),
          h('tbody', visiblePlans.value.length ? visiblePlans.value.map(plan => h('tr', [
            h('td', [h('strong', { class: 'table-primary-text', title: plan.name }, plan.name || '-')]),
            h('td', platformLabel(plan.platform)),
            h('td', sourceLabel(plan.source_mode)),
            h('td', tableText(plan.source_value || '随机推荐流')),
            h('td', tableText(plan.action_label || planActionLabel(plan))),
            h('td', h('span', { class: ['status', plan.archived ? 'cancelled' : 'pending'] }, plan.archived ? '已归档' : '可启动')),
            h('td', tableText(plan.created_at || '-')),
            h('td', { class: 'traffic-row-actions' }, plan.archived ? [
              h('button', { class: 'text-icon-button', onClick: () => restorePlan(plan.id) }, '恢复'),
              h('button', { class: 'text-icon-button danger', onClick: () => deletePlan(plan.id) }, '删除'),
            ] : [
              h('button', { class: 'text-icon-button reserved', onClick: () => startRun(plan.id) }, '启动'),
              h('button', { class: 'text-icon-button', onClick: () => archivePlan(plan.id) }, '归档'),
            ]),
          ])) : [emptyTableRow(8, planArchiveFilter.value === 'archived' ? '暂无已归档计划' : '暂无计划，先在左侧创建')]),
        ]),
      ])
    }

    function renderRunTable() {
      return h('div', { class: 'table-scroll traffic-side-scroll' }, [
        h('table', { class: 'data-table resizable-table traffic-side-table' }, [
          h('thead', [h('tr', ['批次', '计划', '状态', '浏览', '成功', '跳过/失败', '停机原因', '开始时间', '操作'].map(text => h('th', text)))]),
          h('tbody', visibleRuns.value.length ? visibleRuns.value.map(run => h('tr', { class: selectedRun.value?.id === run.id ? 'selected-table-row' : '', onClick: () => selectRun(run.id) }, [
            h('td', [h('strong', { class: 'table-primary-text', title: run.id }, shortId(run.id))]),
            h('td', tableText(run.plan_name || '-')),
            h('td', h('span', { class: ['status', statusClass(run.status)] }, statusText(run.status))),
            h('td', String(run.browsed_count || 0)),
            h('td', String(run.action_success_count || 0)),
            h('td', `${run.skipped_count || 0}/${run.failed_count || 0}`),
            h('td', tableText(run.stop_reason || '-')),
            h('td', tableText(run.started_at || run.created_at || '-')),
            h('td', { class: 'traffic-row-actions' }, run.archived ? [
              h('button', { class: 'text-icon-button', onClick: stopClick(() => restoreRun(run.id)) }, '恢复'),
              h('button', { class: 'text-icon-button danger', onClick: stopClick(() => deleteRun(run.id)) }, '删除'),
            ] : [
              isActiveStatus(run.status) ? h('button', { class: 'text-icon-button', onClick: stopClick(() => stopRun(run.id)) }, '停止') : h('button', { class: 'text-icon-button reserved', onClick: stopClick(() => startRun(run.plan_id)) }, '重试'),
              !isActiveStatus(run.status) ? h('button', { class: 'text-icon-button', onClick: stopClick(() => archiveRun(run.id)) }, '归档') : null,
            ]),
          ])) : [emptyTableRow(9, runArchiveFilter.value === 'archived' ? '暂无已归档批次' : '暂无批次，启动计划后会出现')]),
        ]),
      ])
    }

    function renderLogLine(log: Dict) {
      return h('article', { class: ['traffic-log-item', `log-${log.level}`] }, [
        h('time', log.created_at || ''),
        h('div', { class: 'traffic-log-body' }, [
          h('strong', log.message || '-'),
          log.suggestion ? h('small', `下一步：${log.suggestion}`) : null,
          log.details && log.details !== '{}' ? h('details', [h('summary', '技术详情'), h('pre', log.details)]) : null,
        ]),
      ])
    }

    function isTrafficLogNearBottom(logPanel: HTMLElement) {
      return logPanel.scrollHeight - logPanel.scrollTop - logPanel.clientHeight <= LOG_BOTTOM_THRESHOLD
    }

    function updateTrafficLogFollowState() {
      const logPanel = trafficLogRef.value
      if (!logPanel) return
      trafficLogAutoFollow.value = isTrafficLogNearBottom(logPanel)
    }

    function scrollTrafficLogToBottom(force = false) {
      nextTick(() => {
        const logPanel = trafficLogRef.value
        if (!logPanel) return
        if (!force && !trafficLogAutoFollow.value) return
        logPanel.scrollTop = logPanel.scrollHeight
        trafficLogAutoFollow.value = true
      })
    }

    function renderRecordsTable(rows: Dict[], emptyTitle: string) {
      if (!rows.length) return emptyState({ title: emptyTitle, description: '执行批次后会自动写入记录', icon: DataLine })
      return h('table', { class: 'data-table resizable-table traffic-record-table' }, [
        h('thead', [h('tr', ['时间', '平台', '视频简介', '作者', '点赞数', '评论数', '动作', '评论内容/图片', '状态', '原因', '计划/批次'].map(text => h('th', text)))]),
        h('tbody', rows.map((row: Dict) => h('tr', [
          h('td', tableText(row.created_at || '-')),
          h('td', platformLabel(row.platform)),
          h('td', renderVideoCell(row)),
          h('td', row.author_name || '-'),
          h('td', row.like_count ?? '-'),
          h('td', row.comment_count ?? '-'),
          h('td', renderActionTags(row.actions)),
          h('td', renderCommentCell(row)),
          h('td', h('span', { class: ['status', recordStatusClass(row.status)] }, recordStatusText(row.status))),
          h('td', tableText(row.reason || '-')),
          h('td', [
            h('span', { class: 'table-muted-text', title: row.plan_name || '-' }, row.plan_name || '-'),
            h('small', { class: 'traffic-subtext' }, shortId(row.run_id)),
          ]),
        ]))),
      ])
    }

    function renderLicenseDialog() {
      return h(LicenseDialog, {
        open: licenseOpen.value,
        loading: licenseLoading.value,
        checking: licenseChecking.value,
        info: licenseInfo.value,
        code: licenseCode.value,
        placeholder: '输入引流工作台授权码',
        'onUpdate:code': (value: string) => licenseCode.value = value,
        onClose: () => licenseOpen.value = false,
        onSave: saveLicense,
        onCheck: checkLicense,
        onCopyDevice: async () => {
          await navigator.clipboard.writeText(String(licenseInfo.value.device_code || ''))
          ElMessage.success('设备码已复制')
        },
      })
    }

    function renderTrafficEnvironment() {
      const items = [
        ['Python', trafficEnv.value?.items?.python],
        ['Playwright', trafficEnv.value?.items?.playwright],
        ['CloakBrowser', trafficEnv.value?.items?.cloakbrowser],
        ['浏览器内核', trafficEnv.value?.items?.cloakbrowser_binary],
        ['图片目录', trafficEnv.value?.items?.image_dir],
      ]
      // 环境检查放在右栏，避免挤占文案和图片库的主编辑区。
      return h('div', { class: 'traffic-env-panel' }, [
        sectionTitle({
          title: '环境检查',
          subtitle: trafficEnv.value.summary || '检查 CloakBrowser 和执行目录',
          icon: Monitor,
          tone: trafficEnv.value.ok ? 'green' : 'amber',
          compact: true,
        }),
        h('div', { class: 'env-list' }, items.map(([label, item]: any) => h('div', { class: 'env-item' }, [
          h('span', label),
          h('strong', { class: item?.ok ? 'ok' : 'warn' }, item?.ok ? '正常' : '待处理'),
          h('small', item?.message || '未检查'),
        ]))),
        trafficEnv.value.suggestion ? h('p', { class: 'traffic-env-suggestion' }, trafficEnv.value.suggestion) : null,
        h('div', { class: 'task-card-actions' }, [
          h('button', { class: 'secondary-action', onClick: loadTrafficEnvironment }, [h(Refresh, { class: 'inline-icon' }), '重新检查']),
          h('button', { class: 'primary-action', disabled: envInstalling.value, onClick: installTrafficEnvironment }, envInstalling.value ? '安装中...' : '检查并自动安装'),
        ]),
        envInstallResult.value?.steps?.length ? h('div', { class: 'traffic-install-log' }, envInstallResult.value.steps.map((step: Dict) => h('details', { open: !step.ok }, [
          h('summary', `${step.ok ? '成功' : '失败'}：${step.command}`),
          h('pre', step.output || '无输出'),
        ]))) : null,
      ])
    }

    function actionToggle(key: string, text: string) {
      return h('label', [h('input', {
        type: 'checkbox',
        checked: Boolean(planDraft.value[key]),
        onChange: (event: Event) => planDraft.value[key] = (event.target as HTMLInputElement).checked,
      }), text])
    }

    function settingInput(key: string, text: string) {
      return labelInput(text, settingsDraft.value[key] || '', value => settingsDraft.value[key] = value)
    }

    function settingToggle(key: string, text: string) {
      const value = String(settingsDraft.value[key] ?? 'true').toLowerCase()
      return h('label', [h('input', {
        type: 'checkbox',
        checked: !['0', 'false', 'no', 'off'].includes(value),
        onChange: (event: Event) => settingsDraft.value[key] = (event.target as HTMLInputElement).checked ? 'true' : 'false',
      }), text])
    }

    function labelInput(text: string, value: string, update: (value: string) => void, extraClass = '', placeholder = '') {
      return h('label', { class: ['form-field', extraClass] }, [
        h('span', text),
        h('input', { value, placeholder, onInput: (event: Event) => update((event.target as HTMLInputElement).value) }),
      ])
    }

    function renderTextManager() {
      return h('div', { class: 'form-field field-full traffic-copy-manager' }, [
        h('div', { class: 'traffic-copy-head' }, [
          h('span', `多文案 (${textDraft.value.filter(item => String(item.text || '').trim()).length})`),
          h('button', { class: 'secondary-action', type: 'button', onClick: addTextDraft }, [h(Plus, { class: 'inline-icon' }), '新增文案']),
        ]),
        h('div', { class: 'table-scroll traffic-material-scroll' }, [
          h('table', { class: 'data-table resizable-table traffic-material-table' }, [
            h('thead', [h('tr', ['序号', '文案内容', '启用', '操作'].map(text => h('th', text)))]),
            h('tbody', textDraft.value.length ? textDraft.value.map((item, index) => h('tr', { key: index }, [
              h('td', String(index + 1)),
              h('td', h('input', {
                value: item.text,
                placeholder: `文案 ${index + 1}`,
                onInput: (event: Event) => updateTextDraft(index, { text: (event.target as HTMLInputElement).value }),
              })),
              h('td', h('label', { class: 'traffic-switch' }, [
                h('input', {
                  type: 'checkbox',
                  checked: Boolean(item.enabled),
                  onChange: (event: Event) => updateTextDraft(index, { enabled: (event.target as HTMLInputElement).checked }),
                }),
                '启用',
              ])),
              h('td', h('button', { class: 'text-icon-button danger', type: 'button', title: '删除文案', onClick: () => removeTextDraft(index) }, [h(Delete, { class: 'inline-icon' }), '删除'])),
            ])) : [emptyTableRow(4, '还没有文案。添加后执行评论时会随机抽取一条。')]),
          ]),
        ]),
      ])
    }

    function addTextDraft() {
      textDraft.value = [...textDraft.value, { text: '', enabled: true }]
    }

    function updateTextDraft(index: number, patch: Dict) {
      textDraft.value = textDraft.value.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item)
    }

    function removeTextDraft(index: number) {
      textDraft.value = textDraft.value.filter((_, itemIndex) => itemIndex !== index)
    }

    function addImageDraft() {
      imageDraft.value = [...imageDraft.value, { path: '', preview_url: '', enabled: true }]
    }

    function updateImageDraft(index: number, patch: Dict) {
      imageDraft.value = imageDraft.value.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item)
    }

    function removeImageDraft(index: number) {
      imageDraft.value = imageDraft.value.filter((_, itemIndex) => itemIndex !== index)
    }

    function renderImageManager() {
      return h('div', { class: 'form-field field-full traffic-image-manager' }, [
        h('div', { class: 'traffic-image-head' }, [
          h('span', `图片库 (${imageDraft.value.length})`),
          h('label', { class: ['secondary-action', imageUploading.value ? 'is-disabled' : ''] }, [
            h('input', { class: 'traffic-image-input', type: 'file', accept: 'image/*', multiple: true, disabled: imageUploading.value, onChange: uploadImages }),
            imageUploading.value ? '上传中...' : '上传图片',
          ]),
        ]),
        h('div', { class: 'table-scroll traffic-material-scroll' }, [
          h('table', { class: 'data-table resizable-table traffic-material-table' }, [
            h('thead', [h('tr', ['预览', '文件名/路径', '启用', '操作'].map(text => h('th', text)))]),
            h('tbody', imageDraft.value.length ? imageDraft.value.map((item, index) => h('tr', { key: item.path || index }, [
              h('td', item.preview_url ? h('img', { class: 'traffic-image-thumb', src: item.preview_url, alt: imageName(item.path) }) : h('span', { class: 'table-muted-text' }, '无预览')),
              h('td', h('input', {
                value: item.path,
                placeholder: '本地图片路径',
                onInput: (event: Event) => updateImageDraft(index, { path: (event.target as HTMLInputElement).value }),
              })),
              h('td', h('label', { class: 'traffic-switch' }, [
                h('input', {
                  type: 'checkbox',
                  checked: Boolean(item.enabled),
                  onChange: (event: Event) => updateImageDraft(index, { enabled: (event.target as HTMLInputElement).checked }),
                }),
                '启用',
              ])),
              h('td', h('button', { class: 'text-icon-button danger', type: 'button', title: '删除图片', onClick: () => removeImageDraft(index) }, [h(Delete, { class: 'inline-icon' }), '删除'])),
            ])) : [emptyTableRow(4, '上传图片后会显示预览；也可以新增后手动填写本地路径。')]),
          ]),
        ]),
        h('button', { class: 'secondary-action traffic-add-row-button', type: 'button', onClick: addImageDraft }, [h(Plus, { class: 'inline-icon' }), '新增路径']),
      ])
    }

    function labelSelect(text: string, value: string, options: string[][], update: (value: string) => void) {
      return h('label', { class: 'form-field' }, [
        h('span', text),
        h('select', { value, onChange: (event: Event) => update((event.target as HTMLSelectElement).value) }, options.map(([optionValue, label]) => h('option', { value: optionValue }, label))),
      ])
    }

    function sourceValueLabel() {
      if (planDraft.value.source_mode === 'search_keyword') return '搜索关键词'
      if (planDraft.value.source_mode === 'collected_keyword') return '已采集关键词'
      if (planDraft.value.source_mode === 'competitor_videos') return '竞品视频链接/ID'
      return '来源参数'
    }

    function planActions(plan: Dict) {
      return ['action_like', 'action_collect', 'action_follow', 'action_comment_text', 'action_comment_image'].filter(key => plan[key])
    }

    function planActionLabel(plan: Dict) {
      const labels: Dict = { action_like: '点赞视频', action_collect: '收藏视频', action_follow: '关注作者', action_comment_text: '评论文案', action_comment_image: '评论图片' }
      return planActions(plan).map(key => labels[key]).join('、') || '仅浏览'
    }

    function sourceLabel(value: string) {
      return Object.fromEntries(sourceOptions)[value] || value
    }

    function platformLabel(value: string) {
      return ({ dy: '抖音', ks: '快手', xhs: '小红书' } as Dict)[value] || value || '-'
    }

    function statusText(status: string) {
      return ({ queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', stopped: '已停止' } as Dict)[status] || status
    }

    function statusClass(status: string) {
      if (status === 'completed') return 'succeeded'
      if (status === 'failed') return 'failed'
      if (status === 'running') return 'running'
      if (status === 'stopped') return 'cancelled'
      return 'pending'
    }

    function recordStatusText(status: string) {
      return ({ browsed: '仅浏览', done: '已执行', skipped: '已跳过', failed: '失败', pending: '待处理' } as Dict)[status] || status || '-'
    }

    function recordStatusClass(status: string) {
      if (status === 'done' || status === 'browsed') return 'succeeded'
      if (status === 'failed') return 'failed'
      if (status === 'skipped') return 'cancelled'
      return 'pending'
    }

    function isActiveStatus(status: string) {
      return status === 'queued' || status === 'running'
    }

    function renderArchiveTabs(value: string, update: (value: string) => unknown) {
      return h('div', { class: 'library-list traffic-archive-tabs' }, [
        h('button', { class: value === 'active' ? 'selected' : '', onClick: () => update('active') }, '未归档'),
        h('button', { class: value === 'archived' ? 'selected' : '', onClick: () => update('archived') }, '已归档'),
      ])
    }

    function tableText(value: unknown) {
      const text = String(value || '-')
      return h('span', { class: 'table-muted-text', title: text }, text)
    }

    function emptyTableRow(colspan: number, text: string) {
      return h('tr', [h('td', { class: 'table-empty', colspan }, text)])
    }

    function renderVideoCell(row: Dict) {
      const label = row.video_desc || row.video_id || row.video_url || '-'
      const content = h('span', { class: 'table-primary-text', title: label }, label)
      return row.video_url ? h('a', { class: 'table-primary-link', href: row.video_url, target: '_blank' }, [content]) : content
    }

    function parseActions(value: unknown) {
      let actions = value
      if (typeof value === 'string') {
        try {
          actions = JSON.parse(value)
        } catch {
          actions = value ? [value] : []
        }
      }
      return Array.isArray(actions) ? actions.map(item => String(item)) : []
    }

    function renderActionTags(value: unknown) {
      const items = parseActions(value)
      const labels: Dict = {
        like: '点赞',
        '点赞视频': '点赞',
        collect: '收藏',
        '收藏视频': '收藏',
        follow: '关注',
        '关注作者': '关注',
        comment: '评论',
        comment_text: '评论',
        comment_image: '评论',
        '评论': '评论',
        '仅浏览': '仅浏览',
        '跳过': '跳过',
      }
      const tags = items.length ? items : ['仅浏览']
      return h('div', { class: 'traffic-action-tags' }, tags.map(item => {
        const text = labels[item] || item
        return h('span', { class: ['traffic-action-tag', actionTagClass(text)] }, text)
      }))
    }

    function actionTagClass(text: string) {
      if (text === '点赞') return 'like'
      if (text === '收藏') return 'collect'
      if (text === '关注') return 'follow'
      if (text === '评论') return 'comment'
      if (text === '跳过') return 'skipped'
      return 'browse'
    }

    function renderCommentCell(row: Dict) {
      if (row.comment_image_preview_url) {
        return h('div', { class: 'traffic-comment-preview' }, [
          h('img', { src: row.comment_image_preview_url, alt: imageName(row.comment_image_path || '评论图片') }),
          row.comment_text ? h('span', { class: 'table-muted-text', title: row.comment_text }, row.comment_text) : null,
        ])
      }
      if (row.comment_image_path) return tableText(imageName(row.comment_image_path))
      return tableText(row.comment_text || '-')
    }

    function shortId(value: unknown) {
      const text = String(value || '-')
      return text.length > 10 ? text.slice(0, 8) : text
    }

    function imageLines() {
      return imageDraft.value
        .map(item => ({ path: String(item.path || '').trim(), enabled: Boolean(item.enabled), preview_url: String(item.preview_url || '') }))
        .filter(item => item.path)
    }

    function appendImagePath(image: Dict | string) {
      const path = typeof image === 'string' ? image : String(image.path || '')
      if (!path.trim()) return
      const lines = imageLines()
      if (!lines.some(item => item.path === path)) {
        imageDraft.value = [{ path, preview_url: typeof image === 'string' ? '' : String(image.preview_url || ''), enabled: true }, ...imageDraft.value]
      }
    }

    function imageName(path: string) {
      return path.split(/[\\/]/).pop() || path
    }

    function materialEnabled(value: unknown) {
      return !['0', 'false', 'no', 'off'].includes(String(value ?? true).toLowerCase())
    }

    function stopClick(action: () => unknown) {
      return (event: Event) => {
        event.stopPropagation()
        action()
      }
    }

    function isUserCancel(error: unknown) {
      return error === 'cancel' || error === 'close'
    }

    return () => {
      // 路由只切换当前工作区视图，页面仍复用同一套工作台组件。
      if (view.value === 'traffic-plans') return renderPlanPage()
      if (view.value === 'traffic-monitor') return renderMonitorPage()
      if (view.value === 'traffic-records') return renderRecordsPage()
      return renderSettingsPage()
    }
  },
})
