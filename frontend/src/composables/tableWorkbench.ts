import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { api } from '../shared/api'
import type { Dict } from '../shared/types'


type TablePage = {
  rows: Dict[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

type TableWorkbenchOptions = {
  analyzeJob: (targetType: string, targetId: number) => Promise<unknown>
  refreshTasks: () => Promise<unknown>
  refreshOverview: () => Promise<unknown>
  openTaskLogs: (id: string) => Promise<unknown>
}


export function useTableWorkbench(options: TableWorkbenchOptions) {
  const library = ref('contents')
  const status = ref('')
  const keyword = ref('')
  const rows = ref<Dict[]>([])
  const loading = ref(false)
  const page = ref(1)
  const pageSize = ref(20)
  const total = ref(0)
  const totalPages = ref(1)

  async function load(targetLibrary = library.value, silent = false) {
    if (!silent) loading.value = true
    try {
      const { data } = await api.get<TablePage>(`/tables/${targetLibrary}`, {
        params: { status: status.value, keyword: keyword.value, page: page.value, page_size: pageSize.value },
      })
      rows.value = data.rows
      total.value = Number(data.total || 0)
      page.value = Number(data.page || 1)
      pageSize.value = Number(data.page_size || pageSize.value)
      totalPages.value = Number(data.total_pages || 1)
      // 删除末页最后一条记录后，自动回到仍然存在的最后一页。
      if (!data.rows.length && page.value > totalPages.value) {
        page.value = totalPages.value
        await load(targetLibrary, silent)
      }
    } finally {
      if (!silent) loading.value = false
    }
  }

  async function changeLibrary(value: string) {
    library.value = value
    status.value = ''
    keyword.value = ''
    page.value = 1
    await load(value)
  }

  async function changeFilter(filters: Dict) {
    status.value = filters.status || ''
    keyword.value = filters.keyword || ''
    page.value = 1
    await load()
  }

  async function changePage(payload: Dict) {
    page.value = Number(payload.page || 1)
    pageSize.value = Number(payload.page_size || pageSize.value)
    await load()
  }

  async function updateRow(targetLibrary: string, row: Dict) {
    await api.patch(`/tables/${targetLibrary}/${row.id}`, { values: row })
    ElMessage.success('已保存')
    await load(targetLibrary)
  }

  async function deleteRow(targetLibrary: string, row: Dict, hard?: boolean) {
    const message = targetLibrary === 'target_customers' && !hard ? '目标客户会先隐藏并记录状态事件。确认删除？' : '此操作会删除项目库数据，并记录防重复墓碑。确认继续？'
    await ElMessageBox.confirm(message, '删除确认', { type: 'warning' })
    await api.delete(`/tables/${targetLibrary}/${row.id}`, { params: { hard } })
    ElMessage.success('删除完成')
    await load(targetLibrary)
  }

  async function analyzeRow(targetLibrary: string, row: Dict) {
    if (targetLibrary === 'competitor_candidates') await options.analyzeJob('competitor', row.id)
    if (targetLibrary === 'lead_customers') await options.analyzeJob('lead', row.id)
  }

  async function enrichProfile(_targetLibrary: string, row: Dict) {
    const accountId = row.account_id || row.id
    if (!accountId) {
      ElMessage.error('当前记录缺少账号ID，无法补资料')
      return
    }
    try {
      const { data } = await api.post(`/accounts/${accountId}/profile-enrichment`)
      ElMessage.success(`补资料任务 ${data.id} 已创建`)
      await Promise.all([options.refreshTasks(), options.refreshOverview()])
      await options.openTaskLogs(data.id)
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || '补资料任务创建失败')
    }
  }

  return {
    library,
    status,
    keyword,
    rows,
    loading,
    page,
    pageSize,
    total,
    load,
    changeLibrary,
    changeFilter,
    changePage,
    updateRow,
    deleteRow,
    analyzeRow,
    enrichProfile,
  }
}
