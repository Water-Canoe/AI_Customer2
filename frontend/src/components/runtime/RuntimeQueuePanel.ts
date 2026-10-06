import { defineComponent, h, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Clock, Delete, Refresh, RefreshRight, VideoPause } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { api } from '../../shared/api'
import type { RuntimeJob, RuntimeJobPage } from '../../shared/types'
import { ListPagination, type PageChange } from '../ui/ListPagination'
import { sectionTitle } from '../ui/Workbench'


export const RuntimeQueuePanel = defineComponent({
  name: 'RuntimeQueuePanel',
  props: { refreshSeq: { type: Number, default: 0 }, initialStatus: { type: String, default: '' }, initialKind: { type: String, default: '' }, initialPage: { type: Number, default: 1 }, initialPageSize: { type: Number, default: 20 }, initialJobId: { type: String, default: '' } },
  setup(props) {
    const router = useRouter()
    const route = useRoute()
    const queue = ref<RuntimeJobPage>({ items: [], total: 0, page: 1, page_size: 20, active: {} })
    const filters = reactive({ status: props.initialStatus, kind: props.initialKind, page: props.initialPage, page_size: props.initialPageSize })
    const loading = ref(false)
    const loadError = ref('')
    const operating = ref('')
    const focusedJob = ref<RuntimeJob | null>(null)
    let requestSeq = 0

    function persistFilters() {
      // 定位任务 ID 继续保留在当前路由，侧栏只记住列表条件。
      if (Object.entries(filters).some(([key, value]) => String(route.query[key] ?? '') !== String(value))) void router.replace({ query: { ...route.query, ...filters } })
    }

    async function load(silent = false) {
      const revision = ++requestSeq
      if (!silent) loading.value = true
      try {
        const [{ data }, focused] = await Promise.all([
          api.get<RuntimeJobPage>('/runtime/jobs', { params: { ...filters } }),
          props.initialJobId ? api.get<RuntimeJob>(`/runtime/jobs/${encodeURIComponent(props.initialJobId)}`) : Promise.resolve(null),
        ])
        // 快速切换筛选或详情时，旧响应不能覆盖当前选择。
        if (revision !== requestSeq) return
        queue.value = data
        focusedJob.value = focused?.data || null
        loadError.value = ''
        const totalPages = Math.max(1, Math.ceil(Number(data.total || 0) / filters.page_size))
        if (!(data.items || []).length && filters.page > totalPages) {
          filters.page = totalPages
          persistFilters()
          await load(silent)
        }
      } catch (error: any) {
        if (revision === requestSeq) loadError.value = error?.response?.data?.detail || '任务读取失败，保留上次记录，请重新读取。'
      } finally {
        if (revision === requestSeq) loading.value = false
      }
    }

    function applyFilters() {
      filters.page = 1
      persistFilters()
      void load()
    }

    async function cancel(job: RuntimeJob) {
      operating.value = String(job.id || '')
      try {
        await api.post(`/runtime/jobs/${job.id}/cancel`)
        ElMessage.success('已请求取消任务')
        await load(true)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '取消任务失败')
      } finally {
        operating.value = ''
      }
    }

    async function retry(job: RuntimeJob) {
      operating.value = String(job.id || '')
      try {
        await api.post(`/runtime/jobs/${job.id}/retry`)
        ElMessage.success('任务已重新加入队列')
        await load(true)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '重试任务失败')
      } finally {
        operating.value = ''
      }
    }

    async function remove(job: RuntimeJob) {
      try {
        await ElMessageBox.confirm('只删除运行队列历史，不删除业务数据。确认继续？', '删除队列记录', { type: 'warning' })
      } catch (error: any) {
        if (error === 'cancel' || error === 'close') return
        throw error
      }
      operating.value = String(job.id || '')
      try {
        await api.delete(`/runtime/jobs/${job.id}`)
        ElMessage.success('队列记录已删除')
        await load(true)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '删除队列记录失败')
      } finally {
        operating.value = ''
      }
    }

    onMounted(() => void load())
    onBeforeUnmount(() => { requestSeq += 1 })
    watch(() => props.refreshSeq, () => void load(true))
    watch(() => [props.initialStatus, props.initialKind, props.initialPage, props.initialPageSize] as const, ([status, kind, page, page_size]) => {
      if (JSON.stringify(filters) === JSON.stringify({ status, kind, page, page_size })) return
      Object.assign(filters, { status, kind, page, page_size })
      void load()
    })
    watch(() => props.initialJobId, () => { focusedJob.value = null; void load() })

    return () => {
      const items = queue.value.items || []
      const active = queue.value.active || {}
      const activeCount = Object.values(active).reduce((sum: number, value) => sum + Number(value || 0), 0)
      return h('section', { class: 'runtime-queue-panel' }, [
        h('div', { class: 'runtime-queue-heading' }, [
          sectionTitle({ title: '任务执行', subtitle: `${activeCount} 个执行中 · 共 ${queue.value.total || 0} 条记录`, icon: Clock, tone: activeCount ? 'green' : 'blue', compact: true }),
          h('button', { class: 'text-icon-button', disabled: loading.value, onClick: () => load() }, [h(Refresh, { class: 'inline-icon' }), loading.value ? '刷新中' : '刷新'])
        ]),
        h('div', { class: 'runtime-resource-grid' }, RUNTIME_RESOURCES.map(resource => h('div', { class: 'runtime-resource-card' }, [
          h('span', runtimeResourceLabel(resource)),
          h('strong', String(active[resource] || 0)),
          h('small', '执行中'),
        ]))),
        loadError.value ? h('div', { class: 'workspace-alert', role: 'alert' }, [loadError.value, h('button', { onClick: () => load() }, '重新读取')]) : null,
        focusedJob.value ? h('section', { class: 'runtime-focused-job', 'aria-label': '定位的任务' }, [
          h('div', { class: 'workspace-heading' }, [h('strong', '正在查看这次执行'), h('button', { class: 'text-icon-button', onClick: () => router.push({ path: '/runtime', query: { ...filters } }) }, '返回任务列表')]),
          renderJob(focusedJob.value, operating.value, cancel, retry, remove, value => router.push(String(value.detail_url))),
        ]) : null,
        h('div', { class: 'runtime-queue-filters' }, [
          h('select', { value: filters.status, onChange: (event: Event) => { filters.status = (event.target as HTMLSelectElement).value; applyFilters() } }, [
            h('option', { value: '' }, '全部状态'),
            ...RUNTIME_STATUSES.map(status => h('option', { value: status }, runtimeStatusLabel(status))),
          ]),
          h('select', { value: filters.kind, onChange: (event: Event) => { filters.kind = (event.target as HTMLSelectElement).value; applyFilters() } }, [
            h('option', { value: '' }, '全部任务类型'),
            ...RUNTIME_KINDS.map(kind => h('option', { value: kind }, runtimeKindLabel(kind))),
          ]),
        ]),
        items.length
          ? h('div', { class: 'runtime-job-list' }, items.map(job => renderJob(job, operating.value, cancel, retry, remove, value => router.push(String(value.detail_url)))))
          : h('div', { class: 'diagnostic-empty' }, loading.value ? '正在读取队列...' : '当前没有匹配的运行记录'),
        h(ListPagination, {
          page: filters.page,
          pageSize: filters.page_size,
          total: Number(queue.value.total || 0),
          onChange: (payload: PageChange) => {
            filters.page = payload.page
            filters.page_size = payload.page_size
            persistFilters()
            void load()
          },
        }),
      ])
    }
  }
})


const RUNTIME_RESOURCES = ['browser', 'ai', 'video', 'automation', 'default']
const RUNTIME_STATUSES = ['queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted']
const RUNTIME_KINDS = [
  'crawl_task', 'crawl_batch', 'account_analysis', 'keyword_account_analysis', 'account_customer_intent',
  'ai_job', 'ai_batch', 'traffic_run', 'message_single', 'message_batch', 'automation_run',
  'video_generation', 'content_publish', 'voice_runtime_install', 'account_login', 'account_check',
]


function renderJob(
  job: RuntimeJob,
  operating: string,
  cancel: (job: RuntimeJob) => void,
  retry: (job: RuntimeJob) => void,
  remove: (job: RuntimeJob) => void,
  openDetails: (job: RuntimeJob) => void
) {
  const status = String(job.status || '')
  const busy = operating === String(job.id || '')
  const active = ['queued', 'running'].includes(status)
  return h('article', { class: ['runtime-job-row', `runtime-${status}`] }, [
    h('div', { class: 'runtime-job-main' }, [
      h('div', [
        h('strong', job.display_name || runtimeKindLabel(String(job.kind || ''))),
        h('span', runtimeKindLabel(job.kind))
      ]),
      h('em', { class: `status ${status}` }, runtimeStatusLabel(status))
    ]),
    h('small', runtimeJobSummary(job)),
    h('div', { class: 'runtime-job-actions' }, [
      job.detail_url ? h('button', { onClick: () => openDetails(job) }, '查看详情') : null,
      active ? h('button', { disabled: busy, onClick: () => cancel(job) }, [h(VideoPause, { class: 'inline-icon' }), '取消']) : null,
      job.retry_allowed ? h('button', { disabled: busy, onClick: () => retry(job) }, [h(RefreshRight, { class: 'inline-icon' }), '重试']) : null,
      !active ? h('button', { disabled: busy, onClick: () => remove(job) }, [h(Delete, { class: 'inline-icon' }), '删除']) : null
    ])
  ])
}


export function runtimeKindLabel(kind: string) {
  return ({
    crawl_task: '采集任务',
    crawl_batch: '批量采集',
    account_analysis: '账号分析',
    keyword_account_analysis: '关键词账号分析',
    account_customer_intent: '客户意向分析',
    ai_job: 'AI分析',
    ai_batch: '批量AI分析',
    traffic_run: '引流批次',
    message_single: '单客户自动私信',
    message_batch: '批量自动私信',
    automation_run: '自动化计划',
    video_generation: '视频生成',
    content_publish: '内容发布',
    voice_runtime_install: '语音环境安装',
    account_login: '账号登录',
    account_check: '登录状态检查',
  } as Record<string, string>)[kind] || kind || '未知任务'
}


export function runtimeStatusLabel(status: string) {
  return ({
    queued: '排队中',
    running: '执行中',
    succeeded: '已完成',
    failed: '失败',
    cancelled: '已取消',
    interrupted: '被中断'
  } as Record<string, string>)[status] || status || '未知'
}


export function runtimeJobSummary(job: Partial<RuntimeJob>) {
  const parts = [`资源：${runtimeResourceLabel(String(job.resource || ''))}`, `尝试：${job.attempt || 0}/${job.max_attempts || 1}`]
  if (job.error) parts.push(String(job.error))
  else if (job.started_at) parts.push(`开始：${job.started_at}`)
  else parts.push(`创建：${job.created_at || '-'}`)
  return parts.join(' · ')
}


export function runtimeResourceLabel(resource: string) {
  return ({ browser: '浏览器', ai: 'AI', video: '视频', automation: '自动化编排', default: '普通任务' } as Record<string, string>)[resource] || resource || '未知'
}
