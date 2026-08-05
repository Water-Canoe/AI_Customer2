import { computed, defineComponent, h, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Promotion, Refresh } from '@element-plus/icons-vue'

import { api } from '../shared/api'
import { isAccountFeatureReady } from '../shared/accounts'
import type { Dict } from '../shared/types'
import { emptyState, sectionTitle } from '../components/ui/Workbench'
import { DEFAULT_PAGE_SIZE, ListPagination, type PageChange } from '../components/ui/ListPagination'


export default defineComponent({
  name: 'PublishCenterPage',
  props: { refreshSeq: { type: Number, default: 0 } },
  setup(props) {
    const route = useRoute()
    const router = useRouter()
    const accounts = ref<Dict[]>([])
    const tasks = ref<Dict>({ items: [], total: 0, page: 1, page_size: DEFAULT_PAGE_SIZE })
    const taskStatus = ref('')
    const composer = ref<Dict>({
      open: false,
      source: null,
      account_ids: [],
      title: '',
      description: '',
      tags: '',
      publish_strategy: 'immediate',
      scheduled_at: '',
    })
    const loading = ref(false)
    let timer = 0

    const stats = computed(() => tasks.value.summary || { pending: 0, running: 0, succeeded: 0, review: 0, active: 0 })

    onMounted(async () => {
      await loadAll()
      await loadComposerFromRoute()
      timer = window.setInterval(() => {
        if (Number(stats.value.active || 0) || accounts.value.some(item => item.login_status?.creator?.status === 'checking')) void loadAll(false)
      }, 3000)
    })
    onUnmounted(() => window.clearInterval(timer))
    watch(() => props.refreshSeq, () => void loadAll())
    watch(() => route.fullPath, () => void loadComposerFromRoute())

    async function loadAll(showError = true) {
      try {
        const [accountResult, taskResult] = await Promise.all([
          api.get('/accounts', { params: { feature: 'publish' } }),
          api.get('/content/publish-tasks', { params: { page: tasks.value.page || 1, page_size: tasks.value.page_size || DEFAULT_PAGE_SIZE, status: taskStatus.value } }),
        ])
        accounts.value = accountResult.data
        tasks.value = taskResult.data
      } catch (error: any) {
        if (showError) ElMessage.error(error?.response?.data?.detail || '发布中心加载失败')
      }
    }

    async function taskAction(task: Dict, action: 'cancel' | 'retry') {
      try {
        await api.post(`/content/publish-tasks/${task.id}/${action}`)
        ElMessage.success(action === 'retry' ? '发布任务已重新排队' : '取消请求已发送')
        await loadAll()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '发布任务操作失败')
      }
    }

    async function markPublished(task: Dict) {
      try {
        await api.patch(`/content/publish-tasks/${task.id}/mark-result`, { status: 'succeeded', note: '人工确认已发布' })
        await loadAll()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '人工确认失败')
      }
    }

    async function loadComposerFromRoute() {
      const videoJobId = String(route.query.video_job_id || '')
      const outputName = String(route.query.output_name || '')
      const assetIds = String(route.query.asset_ids || '').split(',').filter(Boolean)
      if (!videoJobId && !assetIds.length) return
      composer.value.open = true
      composer.value.account_ids = accounts.value.filter(item => item.default_features?.includes('publish') && isAccountFeatureReady(item, 'publish')).map(item => item.id)
      if (videoJobId) {
        const { data } = await api.get(`/content/video-jobs/${videoJobId}`)
        composer.value.source = { type: 'video_output', video_job_id: videoJobId, output_name: outputName }
        composer.value.title = data.subject || ''
        composer.value.description = data.script || ''
      } else {
        const assetResults = await Promise.all(assetIds.map(id => api.get(`/content/assets/${id}`)))
        composer.value.source = { type: 'assets', asset_ids: assetIds }
        composer.value.title = String(assetResults[0]?.data?.name || '').replace(/\.[^.]+$/, '')
      }
    }

    async function generateMetadata() {
      if (!composer.value.title.trim()) return ElMessage.warning('请先填写标题')
      loading.value = true
      try {
        const { data } = await api.post('/content/social-metadata', {
          video_subject: composer.value.title,
          video_script: composer.value.description,
          language: 'zh-CN',
          platform: 'tiktok',
        })
        composer.value.title = data.title || composer.value.title
        composer.value.description = data.caption || composer.value.description
        composer.value.tags = (data.hashtags || []).join(', ')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '发布文案生成失败')
      } finally {
        loading.value = false
      }
    }

    async function submitComposer() {
      if (!composer.value.account_ids.length) return ElMessage.warning('请选择发布账号')
      loading.value = true
      try {
        await api.post('/content/publish-tasks', {
          source: composer.value.source,
          account_ids: composer.value.account_ids,
          title: composer.value.title,
          description: composer.value.description,
          tags: String(composer.value.tags || '').split(/[,，\s]+/).filter(Boolean),
          publish_strategy: composer.value.publish_strategy,
          scheduled_at: composer.value.scheduled_at,
          platform_overrides: {},
        })
        ElMessage.success('发布任务已加入队列')
        composer.value.open = false
        await router.replace('/content-publish')
        await loadAll()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '发布任务创建失败')
      } finally {
        loading.value = false
      }
    }

    function renderTasks() {
      return h('section', { class: 'pane publish-center-pane' }, [
        sectionTitle({ title: '发布任务', subtitle: `共 ${tasks.value.total || 0} 条`, icon: Promotion, tone: 'blue', aside: h('div', { class: 'task-card-actions' }, [h('button', { class: 'secondary-action', onClick: () => router.push('/global-settings') }, '管理发布账号'), h('button', { class: 'secondary-action publish-refresh', onClick: () => loadAll() }, [h(Refresh, { class: 'inline-icon' }), '刷新'])]) }),
        h('div', { class: 'publish-stat-grid' }, [statCard('待发布', stats.value.pending), statCard('发布中', stats.value.running), statCard('已成功', stats.value.succeeded), statCard('需要处理', stats.value.review)]),
        h('div', { class: 'publish-task-filter' }, [h('select', { value: taskStatus.value, onChange: (event: Event) => { taskStatus.value = (event.target as HTMLSelectElement).value; tasks.value.page = 1; void loadAll() } }, [h('option', { value: '' }, '全部状态'), ...['queued', 'running', 'succeeded', 'failed', 'review_required', 'cancelled'].map(value => h('option', { value }, taskStatusLabel(value)))])]),
        (tasks.value.items || []).length ? h('div', { class: 'publish-task-list' }, (tasks.value.items || []).map((task: Dict) => h('article', { class: 'publish-task-card' }, [
          h('div', { class: `publish-platform-mark platform-${task.platform}` }, platformLabel(task.platform).slice(0, 1)),
          h('div', { class: 'publish-task-copy' }, [h('strong', String(task.title)), h('span', `${platformLabel(task.platform)} · ${task.account_name}`), h('small', `${taskStageLabel(task.current_stage)} · ${task.progress || 0}% · ${task.scheduled_at || task.created_at}`), task.error ? h('p', { class: 'content-job-error' }, String(task.error)) : null]),
          h('span', { class: `status-pill status-${task.status}` }, taskStatusLabel(task.status)),
          h('div', { class: 'task-card-actions publish-task-actions' }, [
            ['waiting_media', 'queued', 'running'].includes(String(task.status)) ? h('button', { class: 'danger-soft', onClick: () => taskAction(task, 'cancel') }, '取消') : null,
            ['failed', 'review_required', 'cancelled'].includes(String(task.status)) ? h('button', { class: 'primary-soft', onClick: () => taskAction(task, 'retry') }, '重试') : null,
            task.status === 'review_required' ? h('button', { class: 'text-icon-button', onClick: () => markPublished(task) }, '标记已发布') : null,
          ]),
        ]))) : emptyState({ title: '暂无发布任务', description: '从生成记录或内容资产发起第一次发布', icon: Promotion }),
        h(ListPagination, {
          page: Number(tasks.value.page || 1),
          pageSize: Number(tasks.value.page_size || DEFAULT_PAGE_SIZE),
          total: Number(tasks.value.total || 0),
          onChange: (payload: PageChange) => {
            tasks.value.page = payload.page
            tasks.value.page_size = payload.page_size
            void loadAll()
          },
        }),
      ])
    }

    function renderComposer() {
      if (!composer.value.open) return null
      return h('section', { class: 'pane publish-composer' }, [
        sectionTitle({ title: '发布设置', subtitle: '选择账号、文案和发布时间', icon: Promotion, tone: 'purple' }),
        h('div', { class: 'publish-account-options' }, accounts.value.filter(item => isAccountFeatureReady(item, 'publish')).map(account => h('label', [h('input', { type: 'checkbox', checked: composer.value.account_ids.includes(account.id), onChange: (event: Event) => composer.value.account_ids = toggleId(composer.value.account_ids, account.id, (event.target as HTMLInputElement).checked) }), `${platformLabel(account.platform)} · ${account.name}`]))),
        h('label', [h('span', '标题'), h('input', { value: composer.value.title, onInput: (event: Event) => composer.value.title = (event.target as HTMLInputElement).value })]),
        h('label', [h('span', '正文'), h('textarea', { rows: 5, value: composer.value.description, onInput: (event: Event) => composer.value.description = (event.target as HTMLTextAreaElement).value })]),
        h('label', [h('span', '标签'), h('input', { value: composer.value.tags, placeholder: '多个标签用逗号分隔', onInput: (event: Event) => composer.value.tags = (event.target as HTMLInputElement).value })]),
        h('div', { class: 'publish-strategy-row' }, [h('select', { value: composer.value.publish_strategy, onChange: (event: Event) => composer.value.publish_strategy = (event.target as HTMLSelectElement).value }, [h('option', { value: 'immediate' }, '立即发布'), h('option', { value: 'scheduled' }, '定时发布')]), composer.value.publish_strategy === 'scheduled' ? h('input', { type: 'datetime-local', value: composer.value.scheduled_at, onInput: (event: Event) => composer.value.scheduled_at = (event.target as HTMLInputElement).value }) : null]),
        h('div', { class: 'task-card-actions' }, [h('button', { class: 'secondary-action', disabled: loading.value, onClick: generateMetadata }, 'AI生成发布文案'), h('button', { class: 'primary-action', disabled: loading.value, onClick: submitComposer }, loading.value ? '提交中...' : '创建发布任务')]),
      ])
    }

    return () => h('div', { class: 'publish-center-page' }, [
      composer.value.open
        ? h('div', { class: 'publish-center-workspace' }, [renderComposer(), renderTasks()])
        : renderTasks(),
    ])
  },
})

function platformLabel(value: string) { return ({ dy: '抖音', ks: '快手', xhs: '小红书' } as Dict)[value] || value }
function taskStatusLabel(value: string) { return ({ waiting_media: '等待成品', queued: '待发布', running: '发布中', succeeded: '已发布', failed: '失败', review_required: '需要确认', cancelled: '已取消' } as Dict)[value] || value }
function taskStageLabel(value: string) { return ({ waiting_media: '等待视频生成', queued: '等待执行', preparing: '准备素材', uploading: '上传素材', publishing: '提交发布', completed: '发布完成', review_required: '等待人工确认' } as Dict)[value] || value }
function statCard(label: string, value: number) { return h('article', [h('span', label), h('strong', String(value))]) }
function toggleId(values: string[], id: string, checked: boolean) { return checked ? [...new Set([...values, id])] : values.filter(value => value !== id) }
