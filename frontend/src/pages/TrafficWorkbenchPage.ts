import { computed, defineComponent, h, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElDialog, ElDrawer, ElMessage, ElMessageBox } from 'element-plus'
import { usePersistentDraft, draftStatusLabel } from '../shared/usePersistentDraft'
import { useUnsavedSettings } from '../shared/useUnsavedSettings'
import {
  DataLine,
  Delete,
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
import { isAccountFeatureReady } from '../shared/accounts'
import type { Dict, PlatformAccount } from '../shared/types'
import { shouldReplaceDraft } from '../composables/autoSync'
import '../styles/operations.css'
import { COMPACT_PAGE_SIZE, ListPagination, paginateItems, type PageChange } from '../components/ui/ListPagination'
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
  round_video_limit: 5,
  enabled: true,
  account_id: '',
})

// 来源模式取代旧的“定向/随机”独立页面，统一由计划工作台配置。
const sourceOptions = [
  ['random_feed', '随机推荐流'],
  ['competitor_videos', '拓客竞品视频'],
  ['collected_keyword', '已采集关键词'],
  ['search_keyword', '手动搜索关键词'],
]
const SIDE_PAGE_SIZE = 10

export default defineComponent({
  name: 'TrafficWorkbenchPage',
  props: {
    refreshSeq: { type: Number, default: 0 },
  },
  emits: ['navigate-view'],
  setup(props, { emit }) {
    const route = useRoute()
    const router = useRouter()
    const editorOpen = ref(false)
    const runDetailOpen = ref(Boolean(route.query.run))
    const editingId = ref('')
    const savedPlanId = ref('')
    const cleanupError = ref('')
    let startAfterSave = false
    const draftKey = ref('new')
    const plans = ref<Dict[]>([])
    const runs = ref<Dict[]>([])
    const selectedRun = ref<Dict | null>(null)
    const records = ref<Dict>({ rows: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
    const settings = ref<Dict>({ values: {}, texts: [], images: [] })
    const keywords = ref<Dict[]>([])
    const videos = ref<Dict[]>([])
    const sourceVideoQuery = ref('')
    const sourceVideoPage = ref(1)
    const sourceKeywordPage = ref(1)
    const planDraft = ref<Dict>(defaultPlan())
    const persistent = usePersistentDraft({ kind: 'traffic', key: draftKey,
      getPayload: () => ({ ...planDraft.value, return_context: { route: '/traffic-plans', query: { create: '1', ...(editingId.value ? { edit: editingId.value } : {}) }, selection: [] } }),
      applyPayload: payload => {
        planDraft.value = Object.fromEntries(Object.keys(defaultPlan()).map(key => [key, payload[key] ?? (defaultPlan() as Dict)[key]]))
        // 新建草稿记录已保存对象，清理失败后重新打开也不会重复创建。
        if (draftKey.value === 'new' && payload.return_context?.query?.edit) {
          editingId.value = String(payload.return_context.query.edit)
          savedPlanId.value = editingId.value
          cleanupError.value = '计划已保存，请清理草稿后再新建。'
        }
      },
    })
    const settingsDraft = ref<Dict>({})
    const textDraft = ref<Dict[]>([])
    const imageDraft = ref<Dict[]>([])
    const uploadedImages = ref<Dict[]>([])
    const imageUploading = ref(false)
    const settingsDirty = ref(false)
    const settingsSaving = ref(false)
    const trafficEnv = ref<Dict>({})
    const accounts = ref<PlatformAccount[]>([])
    const envInstalling = ref(false)
    const envInstallResult = ref<Dict | null>(null)
    const planArchiveFilter = ref('active')
    const runArchiveFilter = ref('active')
    const planPage = ref(1)
    const runPage = ref(1)
    const detailRecordPage = ref(1)
    const textPage = ref(1)
    const imagePage = ref(1)
    const recordFilters = ref({ query: '', platform: '', status: '', action: '', page: 1, page_size: 20 })
    const loading = ref(false)
    const trafficLogRef = ref<HTMLElement | null>(null)
    const trafficLogAutoFollow = ref(true)
    const LOG_BOTTOM_THRESHOLD = 28
    let settingsMutationSeq = 0
    let settingsRouteRevision = 0

    const view = computed(() => String(route.name || 'traffic-plans'))
    useUnsavedSettings(() => view.value === 'traffic-settings' && (settingsDirty.value || imageUploading.value))
    // 归档数据默认隐藏，只有用户切换到“已归档”时才展示。
    const visiblePlans = computed(() => plans.value.filter(plan => planArchiveFilter.value === 'archived' ? plan.archived : !plan.archived))
    const visibleRuns = computed(() => runs.value.filter(run => (runArchiveFilter.value === 'archived' ? run.archived : !run.archived) && (!route.query.plan || String(run.plan_id) === String(route.query.plan))))
    const pagedPlans = computed(() => pageSlice(visiblePlans.value, planPage.value))
    const pagedRuns = computed(() => pageSlice(visibleRuns.value, runPage.value))
    const filteredSourceVideos = computed(() => {
      const query = sourceVideoQuery.value.trim().toLowerCase()
      if (!query) return videos.value
      return videos.value.filter(item => [item.title, item.description, item.author_name, item.content_id]
        .some(value => String(value || '').toLowerCase().includes(query)))
    })
    const pagedSourceVideos = computed(() => pageSlice(filteredSourceVideos.value, sourceVideoPage.value))
    const pagedSourceKeywords = computed(() => pageSlice(keywords.value, sourceKeywordPage.value))
    const detailRecordPagination = computed(() => paginateItems<Dict>((selectedRun.value?.records || []) as Dict[], detailRecordPage.value, COMPACT_PAGE_SIZE))
    const textPagination = computed(() => paginateItems(textDraft.value, textPage.value, COMPACT_PAGE_SIZE))
    const imagePagination = computed(() => paginateItems(imageDraft.value, imagePage.value, COMPACT_PAGE_SIZE))
    const availableAccounts = computed(() => accounts.value.filter(account =>
      account.platform === planDraft.value.platform && isAccountFeatureReady(account, 'traffic')
    ))

    // 四个子视图分别保存列表条件，不携带其他页的运行或编辑对象。
    function listQuery(name = view.value): Dict {
      if (name === 'traffic-plans') return { archive: planArchiveFilter.value, page: planPage.value }
      if (name === 'traffic-monitor') return { archive: runArchiveFilter.value, page: runPage.value }
      if (name === 'traffic-records') return { ...recordFilters.value }
      return {}
    }
    function persistListQuery(name = view.value) {
      if (name !== view.value) return
      const query = listQuery(name)
      if (Object.entries(query).some(([key, value]) => String(route.query[key] ?? '') !== String(value))) void router.replace({ query: { ...route.query, ...query } })
    }
    function restoreListQuery() {
      const before = JSON.stringify(listQuery())
      const query = route.query
      const page = Math.max(1, Number(query.page || 1))
      if (view.value === 'traffic-plans') {
        if (query.archive) planArchiveFilter.value = String(query.archive)
        if (query.page) planPage.value = page
      } else if (view.value === 'traffic-monitor') {
        if (query.archive) runArchiveFilter.value = String(query.archive)
        if (query.page) runPage.value = page
      } else if (view.value === 'traffic-records') {
        for (const key of ['query', 'platform', 'status', 'action'] as const) if (key in query) recordFilters.value[key] = String(query[key] || '')
        if (query.page) recordFilters.value.page = page
        if (query.page_size) recordFilters.value.page_size = Number(query.page_size)
      }
      return before !== JSON.stringify(listQuery())
    }
    onMounted(async () => { restoreListQuery(); await loadPage(); await openFromRoute() })
    watch(view, (current, previous) => {
      restoreListQuery()
      // 路由真正离开设置后才丢弃输入，取消导航时保留原草稿。
      if (previous === 'traffic-settings' && current !== previous) {
        settingsRouteRevision += 1
        settingsMutationSeq += 1
        settingsDirty.value = false
        applySettings(settings.value)
      }
      void loadPage()
    })
    watch(() => route.query.run, () => { runDetailOpen.value = Boolean(route.query.run); if (view.value === 'traffic-monitor') void loadRuns() })
    watch(() => [route.query.create, route.query.edit, route.query.copy], () => { void openFromRoute() })
    watch(() => route.query.plan, () => { if (view.value === 'traffic-monitor') void loadRuns() })
    watch(() => route.query, () => { if (restoreListQuery()) void loadPage() })
    watch(() => props.refreshSeq, () => loadPage())
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
      if (view.value === 'traffic-plans') await Promise.all([loadPlans(), loadSources(), loadAccounts()])
      else if (view.value === 'traffic-monitor') await Promise.all([loadRuns()])
      else if (view.value === 'traffic-records') await loadRecords()
      else if (view.value === 'traffic-settings') await Promise.all([loadSettings(), loadTrafficEnvironment()])
    }

    async function loadPlans() {
      persistListQuery('traffic-plans')
      const { data } = await api.get('/traffic/plans', { params: { include_archived: planArchiveFilter.value === 'archived' } })
      plans.value = data
    }

    async function loadRuns() {
      const { data } = await api.get('/traffic/runs', { params: { include_archived: runArchiveFilter.value === 'archived', plan_id: String(route.query.plan || '') } })
      runs.value = data
      const requestedRunId = String(route.query.run || '')
      const requestedIndex = visibleRuns.value.findIndex(run => String(run.id) === requestedRunId)
      if (requestedRunId) {
        if (requestedIndex >= 0) runPage.value = Math.floor(requestedIndex / SIDE_PAGE_SIZE) + 1
        await selectRun(requestedRunId)
        persistListQuery('traffic-monitor')
        return
      }
      if (selectedRun.value && !visibleRuns.value.some(run => run.id === selectedRun.value?.id)) selectedRun.value = null
      const selectedId = selectedRun.value?.id
      if (selectedId) await selectRun(selectedId)
      persistListQuery('traffic-monitor')
    }

    async function selectRun(id: string) {
      const { data } = await api.get(`/traffic/runs/${id}`)
      // 轮询只更新当前批次的数据，用户切换批次时才回到第一页。
      if (selectedRun.value?.id !== data.id) detailRecordPage.value = 1
      selectedRun.value = data
    }

    function pageSlice(rows: Dict[], page: number) {
      return paginateItems(rows, page, SIDE_PAGE_SIZE).items
    }

    async function loadRecords() {
      persistListQuery('traffic-records')
      const { data } = await api.get('/traffic/records', { params: recordFilters.value })
      records.value = data
    }

    async function loadSettings() {
      const requestSeq = settingsMutationSeq
      const { data } = await api.get('/traffic/settings')
      if (!shouldReplaceDraft(settingsDirty.value, settingsSaving.value || imageUploading.value, requestSeq, settingsMutationSeq)) return
      applySettings(data)
    }
    function applySettings(data: Dict) {
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

    async function loadTrafficEnvironment() {
      const { data } = await api.get('/traffic/environment-check')
      trafficEnv.value = data
    }

    async function loadAccounts() {
      const { data } = await api.get<PlatformAccount[]>('/accounts', { params: { feature: 'traffic' } })
      accounts.value = data
      selectDefaultAccount()
    }

    function selectDefaultAccount() {
      if (availableAccounts.value.some(account => account.id === planDraft.value.account_id)) return
      const preferred = availableAccounts.value.find(account => account.default_features?.includes('traffic')) || availableAccounts.value[0]
      planDraft.value.account_id = String(preferred?.id || '')
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
      if (platform === 'xhs') {
        ElMessage.info('正在开发')
        return
      }
      planDraft.value.platform = platform
      if (platform === 'ks' && planDraft.value.source_mode !== 'random_feed') {
        planDraft.value.source_mode = 'random_feed'
        planDraft.value.source_value = ''
        ElMessage.info('快手当前先支持随机推荐流')
      }
      if (platform === 'ks' && planDraft.value.action_comment_image) {
        planDraft.value.action_comment_image = false
        ElMessage.info('快手 Web 端暂不支持评论图片')
      }
      selectDefaultAccount()
    }

    function setSourceMode(value: string) {
      if (planDraft.value.platform === 'ks' && value !== 'random_feed') {
        ElMessage.info('快手当前先支持随机推荐流')
        return
      }
      if (planDraft.value.source_mode !== value) planDraft.value.source_value = ''
      planDraft.value.source_mode = value
      sourceVideoPage.value = 1
      sourceKeywordPage.value = 1
    }

    async function openEditor(plan?: Dict, copy = false) {
      if (loading.value || persistent.status.value === 'loading' || cleanupError.value) return
      try {
        if (editorOpen.value) await persistent.flush()
        editingId.value = plan && !copy ? String(plan.id) : ''
        draftKey.value = editingId.value || 'new'
        planDraft.value = plan ? Object.fromEntries(Object.keys(defaultPlan()).map(key => [key, plan[key] ?? (defaultPlan() as Dict)[key]])) : defaultPlan()
        savedPlanId.value = ''
        startAfterSave = false
        editorOpen.value = true
        await readDraft()
        // 复制是用户明确选择的参数，优先于未提交的新建草稿。
        if (plan && copy && !savedPlanId.value) planDraft.value = { ...Object.fromEntries(Object.keys(defaultPlan()).map(key => [key, plan[key] ?? (defaultPlan() as Dict)[key]])), name: `${plan.name || '引流计划'} 副本` }
        selectDefaultAccount()
      } catch (error: any) { ElMessage.error(error?.response?.data?.detail || '计划草稿读取失败') }
    }
    async function readDraft() {
      const stored = await persistent.load()
      // 服务端提交标记不能因读取期间的本地编辑而被忽略。
      if (draftKey.value === 'new' && stored?.submitted) {
        editingId.value = String(stored.payload.return_context?.query?.edit || '')
        savedPlanId.value = editingId.value
        cleanupError.value = '计划已保存，请清理草稿后再新建。'
      }
    }
    async function reloadDraft() {
      try { await readDraft(); selectDefaultAccount() }
      catch (error: any) { ElMessage.error(error?.response?.data?.detail || '计划草稿读取失败') }
    }
    async function openFromRoute() {
      if (view.value !== 'traffic-plans') return
      const id = String(route.query.edit || route.query.copy || '')
      if (id) {
        const plan = plans.value.find(item => String(item.id) === id)
        if (plan) await openEditor(plan, Boolean(route.query.copy))
      } else if (String(route.query.create || '') === '1') await openEditor()
    }
    async function closeEditor() {
      if (loading.value || persistent.status.value === 'loading' || cleanupError.value) return
      try { await persistent.flush(); editorOpen.value = false; await router.replace({ path: '/traffic-plans', query: listQuery('traffic-plans') }) }
      catch { ElMessage.error('草稿保存失败，请重试保存后再关闭') }
    }
    async function openAccounts() {
      try {
        await persistent.flush()
        const returnQuery = new URLSearchParams({ ...listQuery('traffic-plans'), ...(editingId.value ? { edit: editingId.value } : { create: '1' }) })
        await router.push({ path: '/accounts', query: { feature: 'traffic', platform: String(planDraft.value.platform), return_to: `/traffic-plans?${returnQuery}` } })
      } catch { ElMessage.error('草稿保存失败，请重试') }
    }
    async function createPlan(startNow = false) {
      if (loading.value || savedPlanId.value || ['loading', 'error'].includes(persistent.status.value)) return
      loading.value = true
      try {
        const { data } = editingId.value
          ? await api.patch(`/traffic/plans/${editingId.value}`, planDraft.value)
          : await api.post('/traffic/plans', { ...planDraft.value, draft_key: draftKey.value })
        ElMessage.success(editingId.value ? '引流计划已保存' : '引流计划已创建')
        savedPlanId.value = String(data.id || editingId.value)
        editingId.value = savedPlanId.value
        startAfterSave = startNow
        await finishSavedPlan()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || (savedPlanId.value ? '计划已保存，列表刷新失败，请刷新查看' : '计划保存失败'))
      } finally { loading.value = false }
    }
    async function finishSavedPlan() {
      try { await persistent.clear() }
      catch {
        cleanupError.value = `计划已保存，草稿清理失败；不会再次创建。${startAfterSave ? '本次尚未启动。' : ''}`
        // 保存已创建对象的标识，刷新页面后仍可识别这份待清理草稿。
        try { await persistent.flush() }
        catch { cleanupError.value += ' 已保存标识未能写回草稿，请先重试清理。' }
        ElMessage.warning(cleanupError.value)
        return
      }
      cleanupError.value = ''
      editorOpen.value = false
      await router.replace({ path: '/traffic-plans', query: listQuery('traffic-plans') })
      await loadPlans()
      if (startAfterSave) { startAfterSave = false; await startRun(savedPlanId.value) }
    }
    async function retryCleanup() {
      if (loading.value) return
      loading.value = true
      try { await finishSavedPlan() }
      catch { ElMessage.error('计划已保存，列表刷新失败，请刷新查看') }
      finally { loading.value = false }
    }
    function openRun(id: string) {
      runDetailOpen.value = true
      void selectRun(id)
      void router.replace({ path: '/traffic-monitor', query: { ...route.query, run: id } })
    }

    async function startRun(planId: string) {
      try {
        const { data } = await api.post(`/traffic/plans/${planId}/runs`)
        ElMessage.success('引流批次已加入队列')
        selectedRun.value = data
        await router.push({ path: '/traffic-monitor', query: { run: String(data.id) } })
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
      settingsSaving.value = true
      const saveSeq = ++settingsMutationSeq
      const texts = textDraft.value
        .map(item => ({ text: String(item.text || '').trim(), enabled: Boolean(item.enabled) }))
        .filter(item => item.text)
      const images = imageLines().map(item => ({ path: item.path, enabled: Boolean(item.enabled) }))
      try {
        const { data } = await api.put('/traffic/settings', { values: { ...settingsDraft.value }, texts, images })
        settings.value = data
        if (saveSeq === settingsMutationSeq) {
          settingsDirty.value = false
          uploadedImages.value = []
        }
        ElMessage.success('引流设置已保存')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '引流设置保存失败')
      } finally {
        settingsSaving.value = false
      }
    }

    async function uploadImages(event: Event) {
      const input = event.target as HTMLInputElement
      const files = Array.from(input.files || [])
      if (!files.length) return
      const routeRevision = settingsRouteRevision
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
          if (routeRevision !== settingsRouteRevision) return
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

    async function clearRecords() {
      try {
        await ElMessageBox.confirm('会清除计划列表、批次列表、日志、视频明细、操作记录和防重复账本，并重置素材使用次数；配置项、文案、图片和授权不会删除。', '清除引流记录', { type: 'warning' })
        await api.delete('/traffic/records')
        selectedRun.value = null
        ElMessage.success('引流工作台数据已清除')
        await Promise.all([loadPlans(), loadRuns(), loadRecords(), loadSettings()])
      } catch (error: any) {
        if (isUserCancel(error)) return
        ElMessage.error(error?.response?.data?.detail || '引流记录清除失败')
      }
    }

    function renderPlanPage() {
      return h('section', { class: 'panel' }, [
        sectionTitle({ title: '引流计划', subtitle: '配置来源与动作，启动后到执行记录查看结果', icon: Promotion }),
        h('div', { class: 'toolbar' }, [
          renderArchiveTabs(planArchiveFilter.value, async value => { planArchiveFilter.value = value; planPage.value = 1; await loadPlans() }),
          h('button', { class: 'primary-action', disabled: loading.value || Boolean(cleanupError.value), onClick: () => openEditor() }, '新建引流计划'),
        ]),
        renderPlanTable(),
        h(ElDialog, { modelValue: editorOpen.value, title: editingId.value ? '编辑引流计划' : '新建引流计划', width: '900px', closeOnClickModal: false, 'onUpdate:modelValue': (value: boolean) => { if (!value) void closeEditor() } }, () => [
          h('div', { class: 'toolbar' }, [h('span', { class: 'note' }, draftStatusLabel(persistent.status.value)), h('button', { onClick: () => persistent.save().catch(() => undefined) }, '保存草稿'), h('button', { onClick: openAccounts }, '配置执行账号')]),
          persistent.error.value ? h('p', { class: 'operations-error' }, persistent.error.value) : null,
          cleanupError.value ? h('div', { class: 'toolbar' }, [h('span', { class: 'operations-error' }, cleanupError.value), h('button', { disabled: loading.value, onClick: retryCleanup }, '重试清理草稿')]) : persistent.status.value === 'error' ? h('button', { onClick: reloadDraft }, '重新读取草稿') : null,
          renderPlatformTabs(),
          h('div', { class: 'form-grid traffic-plan-form' }, [
            labelInput('计划名称', planDraft.value.name, value => planDraft.value.name = value, 'field-wide', `留空自动生成：${sourceLabel(planDraft.value.source_mode)}-计划ID`),
            labelSelect('执行账号', planDraft.value.account_id, availableAccounts.value.map(account => [account.id, account.name]), value => planDraft.value.account_id = value),
            labelSelect('来源模式', planDraft.value.source_mode, sourceOptions, setSourceMode),
            labelInput('每轮视频上限', String(planDraft.value.round_video_limit || 5), value => planDraft.value.round_video_limit = value.trim() || 5, '', '', 'number'),
            planDraft.value.source_mode === 'random_feed' ? null : planDraft.value.source_mode === 'competitor_videos'
              ? labelTextarea(sourceValueLabel(), planDraft.value.source_value, value => planDraft.value.source_value = value, 'field-wide', '可从下方多选，也可每行填写一个视频链接或ID')
              : labelInput(sourceValueLabel(), planDraft.value.source_value, value => planDraft.value.source_value = value, 'field-wide'),
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
            planDraft.value.platform === 'ks' ? h('li', '快手当前支持随机推荐流、点赞、收藏、关注和文字评论；图片评论和关键词/项目库来源暂不支持。') : null,
          ]),
          renderSourceShortcuts(),
          h('div', { class: 'task-card-actions traffic-form-actions' }, [
            h('button', { class: 'primary-soft', disabled: loading.value || Boolean(savedPlanId.value) || ['loading', 'error'].includes(persistent.status.value) || !planDraft.value.account_id, onClick: () => createPlan(false) }, '保存计划'),
            h('button', { class: 'primary-action', disabled: loading.value || Boolean(savedPlanId.value) || ['loading', 'error'].includes(persistent.status.value) || !planDraft.value.account_id, onClick: () => createPlan(true) }, '保存并启动'),
          ]),

        ]),
      ])
    }

    function renderMonitorPage() {
      const run = selectedRun.value
      return h('section', { class: 'panel' }, [
        sectionTitle({ title: '计划执行记录', subtitle: route.query.plan ? '当前计划的批次记录' : '查看排队、运行和历史批次', icon: Monitor }),
        h('div', { class: 'toolbar' }, [renderArchiveTabs(runArchiveFilter.value, async value => { runArchiveFilter.value = value; runPage.value = 1; await loadRuns() }), h('button', { onClick: loadRuns }, '刷新'), route.query.plan ? h('button', { onClick: () => router.replace('/traffic-monitor') }, '查看全部计划') : null]),
        renderRunTable(),
        h(ElDrawer, { modelValue: runDetailOpen.value, title: run?.plan_name || '执行详情', size: '920px', class: 'operations-detail', 'onUpdate:modelValue': (value: boolean) => { if (!value) { runDetailOpen.value = false; const { run, ...query } = route.query; void router.replace({ query }) } } }, () => [
          run ? h('p', { class: 'note' }, `${statusText(run.status)} · 批次 ${run.id}`) : null,
          run ? renderRunMetrics(run) : null,
          run ? [
            run.stop_reason ? h('div', { class: 'bulk-preview-warning' }, [
              h('li', run.stop_reason),
              run.stop_suggestion ? h('li', run.stop_suggestion) : null,
            ]) : null,
            h('div', { class: 'traffic-log-list', ref: trafficLogRef, onScroll: updateTrafficLogFollowState }, (run.logs || []).length ? run.logs.map(renderLogLine) : [h('p', '暂无日志')]),
            sectionTitle({ title: '已处理视频', subtitle: `${detailRecordPagination.value.total} 条`, icon: VideoPlay, tone: 'green', compact: true }),
            h('div', { class: 'table-scroll traffic-detail-table' }, renderRecordsTable(detailRecordPagination.value.items, '暂无视频记录')),
            h(ListPagination, {
              page: detailRecordPagination.value.page,
              pageSize: detailRecordPagination.value.pageSize,
              total: detailRecordPagination.value.total,
              compact: true,
              onChange: (payload: PageChange) => { detailRecordPage.value = payload.page },
            }),
          ] : emptyState({ title: '请选择批次', description: '选择一个批次查看日志', icon: Monitor }),

        ]),
      ])
    }

    function renderRecordsPage() {
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
          h(ListPagination, {
            page: Number(records.value.page || 1),
            pageSize: Number(records.value.page_size || recordFilters.value.page_size),
            total: Number(records.value.total || 0),
            onChange: (payload: PageChange) => {
              recordFilters.value.page = payload.page
              recordFilters.value.page_size = payload.page_size
              void loadRecords()
            },
          }),
        ]),
      ])
    }

    function renderRunMetrics(run: Dict) {
      return h('div', { class: 'traffic-monitor-metrics' }, [
        metricTile({ label: '浏览视频', value: String(run.browsed_count || 0), tone: 'blue', icon: VideoPlay }),
        metricTile({ label: '成功动作', value: String(run.action_success_count || 0), tone: 'green', icon: Operation }),
        metricTile({ label: '失败/跳过', value: String((run.failed_count || 0) + (run.skipped_count || 0)), tone: 'red', icon: DataLine }),
      ])
    }

    function renderSettingsPage() {
      return h('section', { class: 'panel' }, [
          sectionTitle({ title: '引流设置', subtitle: settingsDirty.value ? '有未保存修改，自动刷新不会覆盖当前草稿' : '文案、图片和执行限额统一在这里维护', icon: Setting, tone: settingsDirty.value ? 'amber' : 'teal' }),
          h('div', { class: 'task-card-actions traffic-settings-actions' }, [
            h('button', { class: 'primary-action', disabled: settingsSaving.value, onClick: saveSettings }, settingsSaving.value ? '保存中...' : '保存设置'),
          ]),
          h('div', { class: 'form-grid traffic-settings-form' }, [
            settingInput('traffic_daily_action_limit', '每日动作上限'),
            settingInput('traffic_action_probability', '操作执行概率%'),
            settingInput('traffic_min_watch_seconds', '最短停留秒数'),
            settingInput('traffic_max_watch_seconds', '最长停留秒数'),
            settingInput('traffic_author_cooldown_hours', '作者冷却小时'),
            settingInput('traffic_stop_after_failures', '连续失败停机次数'),
            h('div', { class: 'toggles field-full' }, [
              settingToggle('traffic_close_browser_on_failure', '失败后关闭浏览器'),
              settingToggle('traffic_headless', '无头浏览器执行'),
            ]),
            renderTextManager(),
            renderImageManager(),
          ]),

        h('details', [h('summary', '运行环境'), renderTrafficEnvironment()]),
        h('details', [h('summary', '数据清理'), h('p', { class: 'note' }, '清理历史记录前请确认保留的数据。'), h('button', { class: 'danger', onClick: clearRecords }, '清除引流记录')]),
      ])
    }

    function renderPlatformTabs() {
      return h('div', { class: 'library-list traffic-platform-tabs' }, [
        h('button', { class: { selected: planDraft.value.platform === 'dy' }, onClick: () => choosePlatform('dy') }, '抖音'),
        h('button', { class: { selected: planDraft.value.platform === 'ks' }, onClick: () => choosePlatform('ks') }, '快手'),
        h('button', { onClick: () => choosePlatform('xhs') }, '小红书'),
      ])
    }

    function renderSourceShortcuts() {
      if (planDraft.value.source_mode === 'collected_keyword') {
        return h('div', { class: 'traffic-source-picker' }, [
          h('div', { class: 'table-scroll traffic-source-shortcuts' }, [
            h('table', { class: 'data-table resizable-table' }, [
              h('thead', [h('tr', ['关键词', '视频数', '操作'].map(text => h('th', text)))]),
              h('tbody', pagedSourceKeywords.value.length ? pagedSourceKeywords.value.map(item => h('tr', [
                h('td', tableText(item.keyword || '-')),
                h('td', String(item.content_count || 0)),
                h('td', h('button', { class: 'text-icon-button reserved', onClick: () => planDraft.value.source_value = item.keyword }, '填入')),
              ])) : [emptyTableRow(3, '暂无已采集关键词')]),
            ]),
          ]),
          renderSidePagination(keywords.value.length, sourceKeywordPage, '关键词'),
        ])
      }
      if (planDraft.value.source_mode === 'competitor_videos') {
        const selected = new Set(splitSourceValues(planDraft.value.source_value))
        const pageAllSelected = pagedSourceVideos.value.length > 0 && pagedSourceVideos.value.every(item => sourceVideoSelected(item, selected))
        return h('div', { class: 'traffic-source-picker' }, [
          h('div', { class: 'traffic-list-toolbar' }, [
            h('input', {
              value: sourceVideoQuery.value,
              placeholder: '搜索视频、作者或ID',
              onInput: (event: Event) => { sourceVideoQuery.value = (event.target as HTMLInputElement).value; sourceVideoPage.value = 1 },
            }),
            h('span', '已选 ' + selected.size + ' / 共 ' + filteredSourceVideos.value.length + ' 条'),
            h('button', {
              class: 'text-icon-button',
              disabled: !pagedSourceVideos.value.length,
              onClick: () => setSourceVideoSelection(pagedSourceVideos.value, !pageAllSelected),
            }, pageAllSelected ? '取消本页' : '选择本页'),
          ]),
          h('div', { class: 'table-scroll traffic-source-shortcuts' }, [
            h('table', { class: 'data-table resizable-table traffic-source-video-table' }, [
              h('thead', [h('tr', ['选择', '视频', '作者', '点赞', '评论', '链接'].map(text => h('th', text)))]),
              h('tbody', pagedSourceVideos.value.length ? pagedSourceVideos.value.map(item => h('tr', [
                h('td', h('input', {
                  type: 'checkbox',
                  checked: sourceVideoSelected(item, selected),
                  onChange: (event: Event) => toggleSourceVideo(item, (event.target as HTMLInputElement).checked),
                })),
                h('td', tableText(item.title || item.description || item.content_id)),
                h('td', tableText(item.author_name || '未知作者')),
                h('td', String(item.like_count || 0)),
                h('td', String(item.comment_count || 0)),
                h('td', sourceVideoUrl(item) ? h('a', { href: sourceVideoUrl(item), target: '_blank', rel: 'noreferrer' }, '打开') : '-'),
              ])) : [emptyTableRow(6, '暂无符合条件的竞品视频')]),
            ]),
          ]),
          renderSidePagination(filteredSourceVideos.value.length, sourceVideoPage, '视频'),
        ])
      }
      return null
    }

    function splitSourceValues(value: string) {
      return String(value || '').split(/[,\r\n]+/).map(item => item.trim()).filter((item, index, rows) => item && rows.indexOf(item) === index)
    }

    function sourceVideoValue(item: Dict) {
      return sourceVideoUrl(item) || String(item.content_id || '').trim()
    }

    function sourceVideoUrl(item: Dict) {
      return String(item.content_url || (item.content_id ? 'https://www.douyin.com/video/' + item.content_id : '')).trim()
    }

    function sourceVideoSelected(item: Dict, selected = new Set(splitSourceValues(planDraft.value.source_value))) {
      return selected.has(sourceVideoValue(item)) || selected.has(String(item.content_id || ''))
    }

    function toggleSourceVideo(item: Dict, checked: boolean) {
      const values = splitSourceValues(planDraft.value.source_value)
        .filter(value => value !== sourceVideoValue(item) && value !== String(item.content_id || ''))
      if (checked && sourceVideoValue(item)) values.push(sourceVideoValue(item))
      planDraft.value.source_value = values.join('\n')
    }

    function setSourceVideoSelection(rows: Dict[], checked: boolean) {
      rows.forEach(item => toggleSourceVideo(item, checked))
    }

    function renderPlanTable() {
      return h('div', { class: 'traffic-side-table-block' }, [
        h('div', { class: 'table-scroll traffic-side-scroll' }, [
          h('table', { class: 'data-table resizable-table traffic-side-table' }, [
            h('thead', [h('tr', ['计划与来源', '每轮上限', '动作组合', '状态', '操作'].map(text => h('th', text)))]),
            h('tbody', pagedPlans.value.length ? pagedPlans.value.map(plan => h('tr', [
              h('td', [h('strong', { class: 'table-primary-text', title: plan.name }, plan.name || '-'), h('small', { class: 'traffic-subtext' }, `${platformLabel(plan.platform)} · ${sourceLabel(plan.source_mode)}`), h('small', { class: 'traffic-subtext', title: plan.source_value }, plan.source_value || '随机推荐流'), h('small', { class: 'traffic-subtext' }, plan.created_at || '')]),
              h('td', String(plan.round_video_limit || 5)),
              h('td', tableText(plan.action_label || planActionLabel(plan))),
              h('td', h('span', { class: ['status', plan.archived ? 'cancelled' : 'pending'] }, plan.archived ? '已归档' : '可启动')),
              h('td', { class: 'traffic-row-actions' }, plan.archived ? [
                h('button', { class: 'text-icon-button', onClick: () => restorePlan(plan.id) }, '恢复'),
                h('button', { class: 'text-icon-button danger', onClick: () => deletePlan(plan.id) }, '删除'),
              ] : [
                h('button', { class: 'text-icon-button reserved', onClick: () => startRun(plan.id) }, '启动'),
                h('button', { class: 'text-icon-button', onClick: () => openEditor(plan) }, '编辑'),
                h('button', { class: 'text-icon-button', onClick: () => openEditor(plan, true) }, '复制'),
                h('button', { class: 'text-icon-button', onClick: () => router.push({ path: '/traffic-monitor', query: { plan: String(plan.id) } }) }, '执行记录'),
                h('button', { class: 'text-icon-button', onClick: () => archivePlan(plan.id) }, '归档'),
              ]),
            ])) : [emptyTableRow(5, planArchiveFilter.value === 'archived' ? '暂无已归档计划' : '暂无计划，请新建引流计划')]),
          ]),
        ]),
        renderSidePagination(visiblePlans.value.length, planPage, '计划'),
      ])
    }

    function renderRunTable() {
      return h('div', { class: 'traffic-side-table-block' }, [
        h('div', { class: 'table-scroll traffic-side-scroll' }, [
          h('table', { class: 'data-table resizable-table traffic-side-table' }, [
            h('thead', [h('tr', ['批次与计划', '状态', '进度', '停机原因', '开始时间', '操作'].map(text => h('th', text)))]),
            h('tbody', pagedRuns.value.length ? pagedRuns.value.map(run => h('tr', { class: selectedRun.value?.id === run.id ? 'selected-table-row' : '', onClick: () => openRun(run.id) }, [
              h('td', [h('strong', { class: 'table-primary-text', title: run.id }, shortId(run.id)), h('small', { class: 'traffic-subtext' }, run.plan_name || '-')]),
              h('td', h('span', { class: ['status', statusClass(run.status)] }, statusText(run.status))),
              h('td', [h('span', `浏览 ${run.browsed_count || 0} · 成功 ${run.action_success_count || 0}`), h('small', { class: 'traffic-subtext' }, `跳过 ${run.skipped_count || 0} · 失败 ${run.failed_count || 0}`)]),
              h('td', tableText(run.stop_reason || '-')),
              h('td', tableText(run.started_at || run.created_at || '-')),
              h('td', { class: 'traffic-row-actions' }, run.archived ? [
                h('button', { class: 'text-icon-button', onClick: stopClick(() => restoreRun(run.id)) }, '恢复'),
                h('button', { class: 'text-icon-button danger', onClick: stopClick(() => deleteRun(run.id)) }, '删除'),
              ] : [
                isActiveStatus(run.status) ? h('button', { class: 'text-icon-button', onClick: stopClick(() => stopRun(run.id)) }, '停止') : h('button', { class: 'text-icon-button reserved', onClick: stopClick(() => startRun(run.plan_id)) }, '重试'),
                !isActiveStatus(run.status) ? h('button', { class: 'text-icon-button', onClick: stopClick(() => archiveRun(run.id)) }, '归档') : null,
              ]),
            ])) : [emptyTableRow(6, runArchiveFilter.value === 'archived' ? '暂无已归档批次' : '暂无批次，启动计划后会出现')]),
          ]),
        ]),
        renderSidePagination(visibleRuns.value.length, runPage, '批次'),
      ])
    }

    function renderSidePagination(total: number, pageRef: { value: number }, _label: string) {
      if (!total) return null
      return h(ListPagination, {
        page: Math.min(Math.max(1, pageRef.value), Math.max(1, Math.ceil(total / SIDE_PAGE_SIZE))),
        pageSize: SIDE_PAGE_SIZE,
        total,
        compact: true,
        onChange: (payload: PageChange) => { pageRef.value = payload.page; if (pageRef === planPage || pageRef === runPage) persistListQuery() },
      })
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
      const disabled = planDraft.value.platform === 'ks' && key === 'action_comment_image'
      return h('label', [h('input', {
        type: 'checkbox',
        disabled,
        checked: Boolean(planDraft.value[key]),
        onChange: (event: Event) => {
          if (disabled) {
            ElMessage.info('快手 Web 端暂不支持评论图片')
            return
          }
          planDraft.value[key] = (event.target as HTMLInputElement).checked
        },
      }), text])
    }

    function settingInput(key: string, text: string) {
      return labelInput(text, settingsDraft.value[key] || '', value => {
        settingsDraft.value[key] = value
        markSettingsDirty()
      })
    }

    function settingToggle(key: string, text: string) {
      const value = String(settingsDraft.value[key] ?? 'true').toLowerCase()
      return h('label', [h('input', {
        type: 'checkbox',
        checked: !['0', 'false', 'no', 'off'].includes(value),
        onChange: (event: Event) => {
          settingsDraft.value[key] = (event.target as HTMLInputElement).checked ? 'true' : 'false'
          markSettingsDirty()
        },
      }), text])
    }

    function labelInput(text: string, value: string, update: (value: string) => void, extraClass = '', placeholder = '', inputType = 'text') {
      return h('label', { class: ['form-field', extraClass] }, [
        h('span', text),
        h('input', { type: inputType, value, placeholder, onInput: (event: Event) => update((event.target as HTMLInputElement).value) }),
      ])
    }

    function labelTextarea(text: string, value: string, update: (value: string) => void, extraClass = '', placeholder = '') {
      return h('label', { class: ['form-field', extraClass] }, [
        h('span', text),
        h('textarea', { value, rows: 3, placeholder, onInput: (event: Event) => update((event.target as HTMLTextAreaElement).value) }),
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
            h('tbody', textDraft.value.length ? textPagination.value.items.map((item, index) => {
              const itemIndex = textPagination.value.start + index
              return h('tr', { key: itemIndex }, [
              h('td', String(itemIndex + 1)),
              h('td', h('input', {
                value: item.text,
                placeholder: `文案 ${itemIndex + 1}`,
                onInput: (event: Event) => updateTextDraft(itemIndex, { text: (event.target as HTMLInputElement).value }),
              })),
              h('td', h('label', { class: 'traffic-switch' }, [
                h('input', {
                  type: 'checkbox',
                  checked: Boolean(item.enabled),
                  onChange: (event: Event) => updateTextDraft(itemIndex, { enabled: (event.target as HTMLInputElement).checked }),
                }),
                '启用',
              ])),
              h('td', h('button', { class: 'text-icon-button danger', type: 'button', title: '删除文案', onClick: () => removeTextDraft(itemIndex) }, [h(Delete, { class: 'inline-icon' }), '删除'])),
            ])}) : [emptyTableRow(4, '还没有文案。添加后执行评论时会随机抽取一条。')]),
          ]),
        ]),
        h(ListPagination, {
          page: textPagination.value.page,
          pageSize: textPagination.value.pageSize,
          total: textPagination.value.total,
          compact: true,
          onChange: (payload: PageChange) => { textPage.value = payload.page },
        }),
      ])
    }

    function addTextDraft() {
      textDraft.value = [...textDraft.value, { text: '', enabled: true }]
      markSettingsDirty()
    }

    function updateTextDraft(index: number, patch: Dict) {
      textDraft.value = textDraft.value.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item)
      markSettingsDirty()
    }

    function removeTextDraft(index: number) {
      textDraft.value = textDraft.value.filter((_, itemIndex) => itemIndex !== index)
      markSettingsDirty()
    }

    function addImageDraft() {
      imageDraft.value = [...imageDraft.value, { path: '', preview_url: '', enabled: true }]
      markSettingsDirty()
    }

    function updateImageDraft(index: number, patch: Dict) {
      imageDraft.value = imageDraft.value.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item)
      markSettingsDirty()
    }

    function removeImageDraft(index: number) {
      imageDraft.value = imageDraft.value.filter((_, itemIndex) => itemIndex !== index)
      markSettingsDirty()
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
            h('tbody', imageDraft.value.length ? imagePagination.value.items.map((item, index) => {
              const itemIndex = imagePagination.value.start + index
              return h('tr', { key: item.path || itemIndex }, [
              h('td', item.preview_url ? h('img', { class: 'traffic-image-thumb', src: item.preview_url, alt: imageName(item.path) }) : h('span', { class: 'table-muted-text' }, '无预览')),
              h('td', h('input', {
                value: item.path,
                placeholder: '本地图片路径',
                onInput: (event: Event) => updateImageDraft(itemIndex, { path: (event.target as HTMLInputElement).value }),
              })),
              h('td', h('label', { class: 'traffic-switch' }, [
                h('input', {
                  type: 'checkbox',
                  checked: Boolean(item.enabled),
                  onChange: (event: Event) => updateImageDraft(itemIndex, { enabled: (event.target as HTMLInputElement).checked }),
                }),
                '启用',
              ])),
              h('td', h('button', { class: 'text-icon-button danger', type: 'button', title: '删除图片', onClick: () => removeImageDraft(itemIndex) }, [h(Delete, { class: 'inline-icon' }), '删除'])),
            ])}) : [emptyTableRow(4, '上传图片后会显示预览；也可以新增后手动填写本地路径。')]),
          ]),
        ]),
        h(ListPagination, {
          page: imagePagination.value.page,
          pageSize: imagePagination.value.pageSize,
          total: imagePagination.value.total,
          compact: true,
          onChange: (payload: PageChange) => { imagePage.value = payload.page },
        }),
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
      if (planDraft.value.source_mode === 'competitor_videos') return '竞品视频链接/ID（每行一条）'
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
        markSettingsDirty()
      }
    }

    function markSettingsDirty() {
      settingsDirty.value = true
      settingsMutationSeq += 1
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

    return () => h('div', { class: 'workspace-page operations-page' }, [
      h('nav', { class: 'tabs toolbar', 'aria-label': '引流工作区' }, [['traffic-plans', '计划'], ['traffic-monitor', '执行记录'], ['traffic-records', '操作记录'], ['traffic-settings', '引流规则']].map(([name, label]) => h('button', { class: view.value === name ? 'active' : '', onClick: () => emit('navigate-view', name) }, label))),
      view.value === 'traffic-plans' ? renderPlanPage() : view.value === 'traffic-monitor' ? renderMonitorPage() : view.value === 'traffic-records' ? renderRecordsPage() : renderSettingsPage(),
    ])
  },
})
