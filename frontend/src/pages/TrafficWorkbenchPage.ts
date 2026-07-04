import { computed, defineComponent, h, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  DataLine,
  Delete,
  Document,
  Key,
  Monitor,
  Operation,
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
  name: '随机推荐引流',
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
    const textDraft = ref('')
    const imageDraft = ref('')
    const uploadedImages = ref<Dict[]>([])
    const imageUploading = ref(false)
    const trafficEnv = ref<Dict>({})
    const envInstalling = ref(false)
    const envInstallResult = ref<Dict | null>(null)
    const douyinLoginOpening = ref(false)
    const recordFilters = ref({ query: '', status: '', action: '', page: 1 })
    const loading = ref(false)

    const view = computed(() => String(route.name || 'traffic-plans'))
    const imagePreviewItems = computed(() => {
      const previews = new Map<string, string>()
      ;[...(settings.value.images || []), ...uploadedImages.value].forEach((item: Dict) => {
        if (item.path && item.preview_url) previews.set(String(item.path), String(item.preview_url))
      })
      return imageLines()
        .map(path => ({ path, preview_url: previews.get(path) || '' }))
        .filter(item => item.preview_url)
    })

    onMounted(loadPage)
    watch(view, () => loadPage())

    async function loadPage() {
      // 四个子页面按需拉取数据，避免进入引流工作台时全量请求。
      if (view.value === 'traffic-plans') await Promise.all([loadPlans(), loadSources()])
      else if (view.value === 'traffic-monitor') await Promise.all([loadRuns()])
      else if (view.value === 'traffic-records') await loadRecords()
      else if (view.value === 'traffic-settings') await Promise.all([loadSettings(), loadTrafficLicense(), loadTrafficEnvironment()])
    }

    async function loadPlans() {
      const { data } = await api.get('/traffic/plans')
      plans.value = data
    }

    async function loadRuns() {
      const { data } = await api.get('/traffic/runs')
      runs.value = data
      if (!selectedRun.value && data.length) await selectRun(data[0].id)
      if (selectedRun.value) await selectRun(selectedRun.value.id)
    }

    async function selectRun(id: string) {
      const { data } = await api.get(`/traffic/runs/${id}`)
      selectedRun.value = data
    }

    async function loadRecords() {
      const { data } = await api.get('/traffic/records', { params: { ...recordFilters.value, page_size: 20 } })
      records.value = data
    }

    async function loadSettings() {
      const { data } = await api.get('/traffic/settings')
      settings.value = data
      uploadedImages.value = []
      settingsDraft.value = { ...(data.values || {}) }
      textDraft.value = (data.texts || []).map((item: Dict) => item.text).join('\n')
      imageDraft.value = (data.images || []).map((item: Dict) => item.path).join('\n')
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
      await api.delete(`/traffic/plans/${planId}`)
      ElMessage.success('计划已删除')
      await loadPlans()
    }

    async function stopRun(runId: string) {
      await api.post(`/traffic/runs/${runId}/stop`)
      ElMessage.success('已发送停止请求')
      await loadRuns()
    }

    async function saveSettings() {
      const texts = textDraft.value.split('\n').map(item => item.trim()).filter(Boolean)
      const images = imageLines()
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
          appendImagePath(String(data.path || ''))
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
      await loadRecords()
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
      return h(SplitPane, { storageKey: 'traffic-plans', side: 'right', defaultSideWidth: 360 }, {
        default: () => h('section', { class: 'content-pane' }, [
          sectionTitle({ title: '创建引流计划', subtitle: '不选动作时就是纯自动刷视频', icon: Promotion, tone: 'teal' }),
          renderPlatformTabs(),
          h('div', { class: 'form-grid' }, [
            labelInput('计划名称', planDraft.value.name, value => planDraft.value.name = value, 'field-wide'),
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
          h('div', { class: 'task-card-actions' }, [
            h('button', { class: 'primary-soft', disabled: loading.value, onClick: () => createPlan(false) }, '保存计划'),
            h('button', { class: 'primary-action', disabled: loading.value, onClick: () => createPlan(true) }, '保存并启动'),
          ]),
        ]),
        side: () => h('aside', { class: 'side-pane' }, [
          sectionTitle({ title: '计划列表', subtitle: `${plans.value.length} 个计划`, icon: Tickets, tone: 'blue' }),
          plans.value.length ? h('div', { class: 'task-list' }, plans.value.map(plan => renderPlanCard(plan))) : emptyState({ title: '暂无计划', description: '先在左侧创建一个引流计划', icon: Document }),
        ]),
      })
    }

    function renderMonitorPage() {
      const run = selectedRun.value
      return h(SplitPane, { storageKey: 'traffic-monitor', side: 'right', defaultSideWidth: 390 }, {
        default: () => h('section', { class: 'content-pane' }, [
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
            h('div', { class: 'log-console' }, (run.logs || []).length ? run.logs.map(renderLogLine) : [h('p', '暂无日志')]),
            sectionTitle({ title: '已处理视频', subtitle: `${(run.items || []).length} 条`, icon: VideoPlay, tone: 'green', compact: true }),
            renderItemsTable(run.items || []),
          ] : emptyState({ title: '请选择批次', description: '右侧选择一个批次查看日志', icon: Monitor }),
        ]),
        side: () => h('aside', { class: 'side-pane' }, [
          sectionTitle({ title: '批次列表', subtitle: '运行状态与历史', icon: Tickets, tone: 'blue' }),
          h('button', { class: 'secondary-action', onClick: loadRuns }, [h(Refresh, { class: 'inline-icon' }), '刷新']),
          runs.value.length ? h('div', { class: 'task-list' }, runs.value.map(renderRunCard)) : emptyState({ title: '暂无批次', description: '启动计划后会出现在这里', icon: Tickets }),
        ]),
      })
    }

    function renderRecordsPage() {
      return h('section', { class: 'table-workspace' }, [
        h('div', { class: 'table-library-bar' }, [
          sectionTitle({ title: '操作记录', subtitle: `共 ${records.value.total || 0} 条`, icon: DataLine, tone: 'green' }),
          h('div', { class: 'table-filters' }, [
            h('input', { placeholder: '搜索视频/作者/评论', value: recordFilters.value.query, onInput: (event: Event) => recordFilters.value.query = (event.target as HTMLInputElement).value }),
            h('select', { value: recordFilters.value.status, onChange: (event: Event) => recordFilters.value.status = (event.target as HTMLSelectElement).value }, [
              h('option', { value: '' }, '全部状态'),
              h('option', { value: 'browsed' }, '仅浏览'),
              h('option', { value: 'done' }, '已执行'),
            ]),
            h('button', { class: 'secondary-action', onClick: loadRecords }, '筛选'),
            h('button', { class: 'text-icon-button danger', onClick: clearRecords }, [h(Delete, { class: 'inline-icon' }), '清除记录']),
          ]),
        ]),
        h('div', { class: 'table-scroll' }, renderRecordsTable()),
        h('div', { class: 'task-list-pagination' }, [
          h('button', { disabled: records.value.page <= 1, onClick: () => { recordFilters.value.page -= 1; loadRecords() } }, '上一页'),
          h('span', `第 ${records.value.page || 1} / ${records.value.total_pages || 1} 页`),
          h('button', { disabled: records.value.page >= records.value.total_pages, onClick: () => { recordFilters.value.page += 1; loadRecords() } }, '下一页'),
        ]),
      ])
    }

    function renderSettingsPage() {
      return h(SplitPane, { storageKey: 'traffic-settings', side: 'right', defaultSideWidth: 320, minSideWidth: 280, maxSideWidth: 420 }, {
        default: () => h('section', { class: 'content-pane traffic-settings-pane' }, [
          sectionTitle({ title: '引流设置', subtitle: '授权、文案、图片、限额统一在这里维护', icon: Setting, tone: 'teal' }),
          h('div', { class: 'task-card-actions' }, [
            h('button', { class: 'secondary-action', onClick: openLicense }, [h(Key, { class: 'inline-icon' }), '授权与设备']),
            h('button', { class: 'primary-action', onClick: saveSettings }, '保存设置'),
          ]),
          h('div', { class: 'form-grid traffic-settings-form' }, [
            settingInput('traffic_round_video_limit', '每轮视频上限'),
            settingInput('traffic_daily_action_limit', '每日动作上限'),
            settingInput('traffic_min_watch_seconds', '最短停留秒数'),
            settingInput('traffic_max_watch_seconds', '最长停留秒数'),
            settingInput('traffic_author_cooldown_hours', '作者冷却小时'),
            settingInput('traffic_stop_after_failures', '连续失败停机次数'),
            labelTextarea('多文案', textDraft.value, value => textDraft.value = value, '一行一条，发送时随机抽取'),
            renderImageManager(),
          ]),
          renderLicenseDialog(),
        ]),
        side: () => h('aside', { class: 'side-pane traffic-settings-side' }, [
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
      return h('div', { class: 'library-list' }, [
        h('button', { class: { selected: planDraft.value.platform === 'dy' }, onClick: () => choosePlatform('dy') }, '抖音'),
        h('button', { onClick: () => choosePlatform('ks') }, '快手'),
        h('button', { onClick: () => choosePlatform('xhs') }, '小红书'),
      ])
    }

    function renderSourceShortcuts() {
      if (planDraft.value.source_mode === 'collected_keyword') {
        return h('div', { class: 'library-list' }, keywords.value.slice(0, 20).map(item => h('button', { onClick: () => planDraft.value.source_value = item.keyword }, `${item.keyword} (${item.content_count})`)))
      }
      if (planDraft.value.source_mode === 'competitor_videos') {
        return h('div', { class: 'task-list' }, videos.value.slice(0, 6).map(item => h('button', { class: 'task-row', onClick: () => planDraft.value.source_value = item.content_url || item.content_id }, [
          h('strong', item.title || item.description || item.content_id),
          h('small', item.author_name || '未知作者'),
        ])))
      }
      return null
    }

    function renderPlanCard(plan: Dict) {
      return h('article', { class: 'task-row' }, [
        h('div', { class: 'task-row-head' }, [
          h('div', { class: 'task-title' }, [h('strong', plan.name), h('small', `${sourceLabel(plan.source_mode)} · ${plan.action_label}`)]),
          h('span', { class: 'status pending' }, plan.platform === 'dy' ? '抖音' : '开发中'),
        ]),
        h('div', { class: 'task-card-actions' }, [
          h('button', { class: 'primary-soft', onClick: () => startRun(plan.id) }, '启动'),
          h('button', { class: 'danger', onClick: () => deletePlan(plan.id) }, '删除'),
        ]),
      ])
    }

    function renderRunCard(run: Dict) {
      return h('article', { class: ['task-row', selectedRun.value?.id === run.id ? 'selected' : ''], onClick: () => selectRun(run.id) }, [
        h('div', { class: 'task-row-head' }, [
          h('div', { class: 'task-title' }, [h('strong', run.plan_name), h('small', run.stop_reason || sourceLabel(run.source_mode))]),
          h('span', { class: ['status', statusClass(run.status)] }, statusText(run.status)),
        ]),
        h('div', { class: 'task-card-actions' }, [
          isActiveStatus(run.status) ? h('button', { class: 'warning-soft', onClick: (event: Event) => { event.stopPropagation(); stopRun(run.id) } }, '停止') : null,
        ]),
      ])
    }

    function renderLogLine(log: Dict) {
      return h('p', { class: `log-${log.level}` }, [
        h('time', log.created_at || ''),
        ` ${log.message}`,
        log.suggestion ? h('small', `  下一步：${log.suggestion}`) : null,
        log.details && log.details !== '{}' ? h('details', [h('summary', '技术详情'), h('pre', log.details)]) : null,
      ])
    }

    function renderItemsTable(items: Dict[]) {
      if (!items.length) return emptyState({ title: '暂无视频记录', description: '任务开始处理视频后会显示在这里', icon: VideoPlay })
      return h('table', { class: 'data-table' }, [
        h('thead', [h('tr', ['视频', '作者', '动作', '状态'].map(text => h('th', text)))]),
        h('tbody', items.map(item => h('tr', [
          h('td', item.video_desc || item.video_id),
          h('td', item.author_name || '-'),
          h('td', item.actions_done || '[]'),
          h('td', h('span', { class: 'status succeeded' }, item.status || '-')),
        ]))),
      ])
    }

    function renderRecordsTable() {
      const rows = records.value.rows || []
      if (!rows.length) return emptyState({ title: '暂无操作记录', description: '执行批次后会自动写入记录', icon: DataLine })
      return h('table', { class: 'data-table resizable-table' }, [
        h('thead', [h('tr', ['视频简介', '评论内容', '作者', '视频点赞数', '评论数', '操作', '时间'].map(text => h('th', text)))]),
        h('tbody', rows.map((row: Dict) => h('tr', [
          h('td', row.video_url ? h('a', { class: 'table-primary-link', href: row.video_url, target: '_blank' }, row.video_desc || row.video_id) : (row.video_desc || row.video_id)),
          h('td', row.comment_text || row.comment_image_path || '-'),
          h('td', row.author_name || '-'),
          h('td', row.like_count ?? '-'),
          h('td', row.comment_count ?? '-'),
          h('td', (row.actions || []).join('、') || '仅浏览'),
          h('td', row.created_at || '-'),
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
        ['Chromium', trafficEnv.value?.items?.chromium],
        ['图片目录', trafficEnv.value?.items?.image_dir],
      ]
      // 环境检查放在右栏，避免挤占文案和图片库的主编辑区。
      return h('div', { class: 'traffic-env-panel' }, [
        sectionTitle({
          title: '环境检查',
          subtitle: trafficEnv.value.summary || '检查 Playwright 和执行目录',
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

    function labelInput(text: string, value: string, update: (value: string) => void, extraClass = '') {
      return h('label', { class: ['form-field', extraClass] }, [
        h('span', text),
        h('input', { value, onInput: (event: Event) => update((event.target as HTMLInputElement).value) }),
      ])
    }

    function labelTextarea(text: string, value: string, update: (value: string) => void, placeholder = '') {
      return h('label', { class: 'form-field field-full' }, [
        h('span', text),
        h('textarea', { rows: 6, placeholder, value, onInput: (event: Event) => update((event.target as HTMLTextAreaElement).value) }),
      ])
    }

    function renderImageManager() {
      return h('div', { class: 'form-field field-full traffic-image-manager' }, [
        h('div', { class: 'traffic-image-head' }, [
          h('span', '图片路径'),
          h('label', { class: ['secondary-action', imageUploading.value ? 'is-disabled' : ''] }, [
            h('input', { class: 'traffic-image-input', type: 'file', accept: 'image/*', multiple: true, disabled: imageUploading.value, onChange: uploadImages }),
            imageUploading.value ? '上传中...' : '上传图片',
          ]),
        ]),
        h('textarea', {
          rows: 5,
          placeholder: '上传图片后自动填入；也可以一行一个本地图片路径',
          value: imageDraft.value,
          onInput: (event: Event) => imageDraft.value = (event.target as HTMLTextAreaElement).value,
        }),
        imagePreviewItems.value.length
          ? h('div', { class: 'traffic-image-preview-grid' }, imagePreviewItems.value.map(item => h('figure', { class: 'traffic-image-preview' }, [
            h('img', { src: item.preview_url, alt: imageName(item.path) }),
            h('figcaption', imageName(item.path)),
          ])))
          : h('small', { class: 'traffic-image-help' }, '上传到图片库后会在这里显示预览。'),
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

    function isActiveStatus(status: string) {
      return status === 'queued' || status === 'running'
    }

    function imageLines() {
      return imageDraft.value.split('\n').map(item => item.trim()).filter(Boolean)
    }

    function appendImagePath(path: string) {
      if (!path) return
      const lines = imageLines()
      if (!lines.includes(path)) lines.push(path)
      imageDraft.value = lines.join('\n')
    }

    function imageName(path: string) {
      return path.split(/[\\/]/).pop() || path
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
