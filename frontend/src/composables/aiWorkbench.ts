import { ref } from 'vue'
import { ElMessage } from 'element-plus'

import { api } from '../shared/api'
import { confirmBulkPreview } from '../shared/bulkPreview'
import type { Dict } from '../shared/types'


type AiWorkbenchOptions = {
  refreshRelated: () => Promise<unknown>
}


export function useAiWorkbench(options: AiWorkbenchOptions) {
  const workbench = ref<Dict>({})
  const query = ref<Dict>({ tab: 'competitors', keyword: '', status: '', result: '', page: 1, page_size: 10 })
  let requestSeq = 0

  async function load() {
    const revision = ++requestSeq
    const { data } = await api.get('/ai/workbench', { params: { ...query.value } })
    // 快速切换筛选时，较慢的旧响应不能替换当前列表。
    if (revision !== requestSeq) return
    workbench.value = data
    if (!data.items?.length && Number(query.value.page || 1) > Number(data.total_pages || 1)) {
      query.value.page = Number(data.total_pages || 1)
      await load()
    }
  }

  async function changeQuery(value: Dict) {
    query.value = { ...query.value, ...value }
    await load()
  }

  // AI操作结束后统一刷新工作台及其关联页面，避免各操作维护不同的刷新清单。
  async function refresh() {
    await Promise.allSettled([load(), options.refreshRelated()])
  }

  async function createJob(targetType: string, targetId: number) {
    try {
      await api.post('/ai/jobs', { target_type: targetType, target_id: targetId })
      ElMessage.success('AI分析已加入队列')
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || 'AI分析失败')
    } finally {
      await refresh()
    }
  }

  async function createBatchJobs(targetType: string) {
    try {
      await confirmBulkPreview({ action: 'ai_analyze', target_type: targetType, filters: { unanalysed_only: true } }, '一键分析预览')
      // 省略对象 ID，由服务端在入队事务内重新选择全部未分析对象。
      const { data } = await api.post('/ai/jobs/batch', { target_type: targetType })
      if (data.length) ElMessage.success(`已将 ${data.length} 个AI分析任务加入队列`)
      else ElMessage.info('没有未分析对象，已分析或正在处理的对象不会重复入队')
    } catch (error: any) {
      if (error === 'cancel' || error === 'close') return
      ElMessage.error(error?.response?.data?.detail || '批量AI分析失败')
    } finally {
      await refresh()
    }
  }

  async function deleteNonCompetitors(targetIds: number[]) {
    const ids = uniqueIds(targetIds)
    if (!ids.length) {
      ElMessage.info('当前筛选范围没有可删除的非竞品')
      return
    }
    try {
      await confirmBulkPreview({ action: 'delete_non_competitors', target_type: 'competitor', target_ids: ids }, '删除非竞品预览')
      const { data } = await api.post('/ai/workbench/non-competitors/delete', { target_ids: ids })
      showDeleteResult(data, '非竞品账号', '账号')
    } catch (error: any) {
      if (error === 'cancel' || error === 'close') return
      ElMessage.error(error?.response?.data?.detail || '删除非竞品失败')
    } finally {
      await refresh()
    }
  }

  async function deleteNonCustomers(targetIds: number[]) {
    const ids = uniqueIds(targetIds)
    if (!ids.length) {
      ElMessage.info('当前筛选范围没有可删除的非客户')
      return
    }
    try {
      await confirmBulkPreview({ action: 'delete_non_customers', target_type: 'lead', target_ids: ids }, '删除非客户预览')
      const { data } = await api.post('/ai/workbench/non-customers/delete', { target_ids: ids })
      showDeleteResult(data, '非客户', '客户')
    } catch (error: any) {
      if (error === 'cancel' || error === 'close') return
      ElMessage.error(error?.response?.data?.detail || '删除非客户失败')
    } finally {
      await refresh()
    }
  }

  async function retryJob(jobId: string) {
    try {
      await api.post(`/ai/jobs/${jobId}/retry`)
      ElMessage.success('重试已加入队列')
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || '重试失败')
    } finally {
      await refresh()
    }
  }

  async function retryJobs(jobIds: string[]) {
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
      await refresh()
    }
  }

  return {
    workbench,
    query,
    load,
    changeQuery,
    createJob,
    createBatchJobs,
    deleteNonCompetitors,
    deleteNonCustomers,
    retryJob,
    retryJobs,
  }
}

function uniqueIds(ids: number[]) {
  return Array.from(new Set(ids.map(Number).filter(Boolean)))
}

function showDeleteResult(data: Dict, itemName: string, skippedName: string) {
  if (data.deleted) ElMessage.success(`已删除 ${data.deleted} 个${itemName}`)
  else ElMessage.info(`没有删除任何${itemName}`)
  if (data.skipped?.length) ElMessage.warning(`已跳过 ${data.skipped.length} 个不符合删除条件的${skippedName}`)
  if (data.failed?.length) ElMessage.error(`有 ${data.failed.length} 个${skippedName}删除失败`)
}
