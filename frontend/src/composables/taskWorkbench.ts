import { ref } from 'vue'
import type { Router } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import type { PageChange } from '../components/ui/ListPagination'
import { api } from '../shared/api'
import type { Dict, TaskPage, TaskRecord } from '../shared/types'


type TaskWorkbenchOptions = {
  router: Pick<Router, 'push' | 'replace'>
  refreshRelated: () => Promise<unknown>
}


export function useTaskWorkbench(options: TaskWorkbenchOptions) {
  const tasks = ref<TaskRecord[]>([])
  const page = ref(1)
  const pageSize = ref(10)
  const total = ref(0)
  const totalPages = ref(1)
  const query = ref('')
  const selected = ref<TaskRecord | null>(null)
  const diagnostics = ref<Dict>({})
  const dedupSummary = ref<Dict>({})
  const retryDraft = ref<(TaskRecord & { retry_token?: number }) | null>(null)
  let listRequestSeq = 0

  // 采集列表上下文随操作写入地址，详情和新建入口沿用相同筛选。
  const listQuery = () => ({ query: query.value, page: String(page.value), page_size: String(pageSize.value) })

  async function fetchTask(id: string) {
    const { data } = await api.get<TaskRecord>(`/tasks/${id}`)
    return data
  }

  async function loadTasks() {
    const requestSeq = ++listRequestSeq
    const { data } = await api.get<TaskPage>('/tasks', {
      params: { page: page.value, page_size: pageSize.value, query: query.value },
    })
    if (requestSeq !== listRequestSeq) return
    tasks.value = data.items
    total.value = Number(data.total || 0)
    page.value = Number(data.page || 1)
    pageSize.value = Number(data.page_size || pageSize.value)
    totalPages.value = Number(data.total_pages || 1)
    if (!data.items.length && page.value > totalPages.value) {
      page.value = totalPages.value
      await loadTasks()
      return
    }
    // 列表不会自动打开首条详情；只有用户选择或创建成功才加载详情。
  }

  async function loadDiagnostics(id?: string) {
    const taskId = id || selected.value?.id
    if (!taskId) {
      diagnostics.value = {}
      dedupSummary.value = {}
      return
    }
    const [diagnosticResult, dedupResult] = await Promise.allSettled([
      api.get(`/tasks/${taskId}/diagnostics`),
      api.get(`/tasks/${taskId}/dedup-summary`),
    ])
    diagnostics.value = diagnosticResult.status === 'fulfilled' ? diagnosticResult.value.data : {}
    dedupSummary.value = dedupResult.status === 'fulfilled' ? dedupResult.value.data : {}
  }

  async function changePage(payload: PageChange) {
    page.value = Number(payload.page || 1)
    pageSize.value = Number(payload.page_size || pageSize.value)
    selected.value = null
    diagnostics.value = {}
    dedupSummary.value = {}
    await options.router.replace({ path: '/acquisition', query: listQuery() })
    await loadTasks()
    await loadDiagnostics()
  }

  async function changeQuery(value: string) {
    query.value = String(value || '').trim()
    page.value = 1
    selected.value = null
    diagnostics.value = {}
    dedupSummary.value = {}
    await options.router.replace({ path: '/acquisition', query: listQuery() })
    await loadTasks()
    await loadDiagnostics()
  }

  async function refreshSelected() {
    const taskId = selected.value?.id
    if (!taskId) return
    try {
      selected.value = await fetchTask(taskId)
      await loadDiagnostics(taskId)
    } catch (error: any) {
      if (error?.response?.status !== 404) throw error
      selected.value = null
      diagnostics.value = {}
      dedupSummary.value = {}
    }
  }

  async function createTask(payload: Dict, onCreated?: (task: TaskRecord) => Promise<void> | void, onSettled?: () => void) {
    let created = false
    try {
      const { data } = await api.post<TaskRecord>('/tasks', payload)
      created = true
      ElMessage.success(`任务 ${data.id} 已创建`)
      await onCreated?.(data)
      await loadTasks()
      selected.value = await fetchTask(data.id)
      await loadDiagnostics(data.id)
      await options.router.push({ path: '/acquisition', query: { ...listQuery(), task: data.id } })
    } catch (error: any) {
      ElMessage.error(created ? '任务已创建，结果刷新失败，请刷新查看' : error?.response?.data?.detail || '任务创建失败')
    } finally { onSettled?.() }
  }

  async function openLogs(id: string) {
    selected.value = await fetchTask(id)
    await loadDiagnostics(id)
    await options.router.push({ path: '/acquisition', query: { ...listQuery(), task: id } })
  }

  async function selectTask(id: string) {
    selected.value = await fetchTask(id)
    await loadDiagnostics(id)
  }

  async function archiveTask(id: string) {
    await api.post(`/tasks/${id}/archive`)
    ElMessage.success('任务已归档')
    await loadTasks()
  }

  async function cancelTask(id: string) {
    await api.post(`/tasks/${id}/cancel`)
    ElMessage.success('任务已取消')
    await Promise.allSettled([loadTasks(), options.refreshRelated()])
    selected.value = await fetchTask(id)
    await loadDiagnostics(id)
  }

  function retryTask(task: TaskRecord) {
    retryDraft.value = { ...task, retry_token: Date.now() }
    void options.router.push({ path: '/acquisition', query: { ...listQuery(), create: '1' } })
    ElMessage.success('已带入失败任务参数，请确认后重新启动')
  }

  function consumeRetryDraft() {
    retryDraft.value = null
  }

  async function deleteTask(id: string) {
    await ElMessageBox.confirm('任务删除会同步清理项目库和原始采集映射。确认继续？', '硬删除确认', { type: 'warning' })
    await api.delete(`/tasks/${id}`)
    ElMessage.success('任务已硬删除')
    selected.value = null
    diagnostics.value = {}
    dedupSummary.value = {}
    await Promise.allSettled([loadTasks(), options.refreshRelated()])
  }

  return {
    tasks,
    page,
    pageSize,
    total,
    query,
    selected,
    diagnostics,
    dedupSummary,
    retryDraft,
    loadTasks,
    fetchTask,
    loadDiagnostics,
    changePage,
    changeQuery,
    refreshSelected,
    createTask,
    openLogs,
    selectTask,
    archiveTask,
    cancelTask,
    retryTask,
    consumeRetryDraft,
    deleteTask,
  }
}
