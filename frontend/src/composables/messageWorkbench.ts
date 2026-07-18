import { ref, type Ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { api } from '../shared/api'
import type { Dict } from '../shared/types'


type MessageWorkbenchOptions = {
  settings: Ref<Dict>
  refreshRelated: () => Promise<unknown>
}


export function useMessageWorkbench(options: MessageWorkbenchOptions) {
  // 私信页的数据与操作集中在这里，应用壳只负责路由和跨页面刷新。
  const keywords = ref<Dict[]>([])
  const customers = ref<Dict>({ rows: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
  const detail = ref<Dict>({})
  const loading = ref(false)
  const filters = ref<Dict>({ keyword: '', status: '待私信', query: '', page: 1, page_size: 20 })
  const batches = ref<Dict>({ batches: [], active: null, items: [] })

  async function loadBatches(batchId?: string) {
    // 未明确切换批次时保留当前选择，避免自动刷新跳回最新批次。
    const selectedId = batchId === undefined
      ? String(batches.value.selected_batch_id || '')
      : String(batchId || '')
    const { data } = await api.get('/message-workbench/auto-message-batches', {
      params: selectedId ? { batch_id: selectedId } : {},
    })
    batches.value = data
  }

  async function load(silent = false) {
    if (!silent) loading.value = true
    try {
      const [keywordResponse, customerResponse] = await Promise.all([
        api.get('/message-workbench/keywords'),
        api.get('/message-workbench/customers', { params: filters.value }),
        loadBatches(),
      ])
      keywords.value = keywordResponse.data
      customers.value = customerResponse.data
      const detailLeadId = detail.value?.customer?.lead_id
      if (detailLeadId) {
        try {
          const response = await api.get(`/message-workbench/customers/${detailLeadId}`)
          detail.value = response.data
        } catch (error: any) {
          if (error?.response?.status === 404) detail.value = {}
          else throw error
        }
      }
    } finally {
      if (!silent) loading.value = false
    }
  }

  async function refreshAllMessageData() {
    await Promise.allSettled([load(true), options.refreshRelated()])
  }

  async function changeFilter(values: Dict) {
    filters.value = { ...filters.value, ...values, page: Number(values.page || 1) }
    await load()
  }

  async function selectCustomer(leadId: number | string) {
    const { data } = await api.get(`/message-workbench/customers/${leadId}`)
    detail.value = data
  }

  function closeDetail() {
    detail.value = {}
  }

  async function updateFollowStatus(row: Dict, status: string) {
    const leadId = row.lead_id || row.id
    if (!leadId) return ElMessage.error('当前客户缺少线索ID，无法修改状态')
    try {
      await api.patch(`/overview/customers/${leadId}/follow-status`, {
        follow_status: status,
        note: '私信工作台修改跟进状态',
      })
      ElMessage.success(`已更新为“${status}”`)
      await refreshAllMessageData()
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || '修改跟进状态失败')
    }
  }

  async function messageCustomer(row: Dict) {
    const leadId = row.lead_id || row.id
    const script = selectDmScript(options.settings.value, row.script)
    const profileUrl = String(row.profile_url || '').trim()
    if (!leadId) return ElMessage.error('当前客户缺少线索ID，无法标记私信')
    if (!script.text) return ElMessage.error(script.emptyMessage)
    if (!profileUrl) return ElMessage.error('当前客户缺少主页链接，无法打开主页')
    try {
      const homepage = window.open(profileUrl, '_blank')
      if (!homepage) return ElMessage.warning('浏览器拦截了主页窗口，未复制话术，也未修改跟进状态')
      homepage.opener = null
      await navigator.clipboard.writeText(script.text)

      const currentStatus = String(row.follow_status || row.screening_status || '未私信')
      if (['待筛选', '未分析', '目标客户', '未私信'].includes(currentStatus)) {
        await api.patch(`/overview/customers/${leadId}/follow-status`, {
          follow_status: '已私信',
          note: `私信工作台：复制${script.label}并打开客户主页`,
          record_message_attempt: true,
        })
        ElMessage.success(`${script.label}已复制，客户主页已打开，跟进状态已更新为“已私信”`)
      } else {
        ElMessage.success(`${script.label}已复制，客户主页已打开；当前状态“${currentStatus}”未回退`)
      }
      await refreshAllMessageData()
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || '私信操作失败')
    }
  }

  async function autoMessageCustomer(row: Dict) {
    const leadId = row.lead_id || row.id
    const script = selectDmScript(options.settings.value, row.script)
    if (!leadId) return ElMessage.error('当前客户缺少线索ID，无法自动私信')
    if (row.platform !== 'dy') return ElMessage.error('自动私信当前只支持抖音客户')
    if (!script.text) return ElMessage.error(script.emptyMessage)
    try {
      const { data } = await api.post(`/message-workbench/customers/${leadId}/auto-message`, {
        dry_run: Boolean(options.settings.value.auto_dm_fill_only),
        timeout_seconds: Number(options.settings.value.auto_dm_timeout_seconds || 0),
        message_script: script.text,
        script_label: script.label,
        account_id: String(row.account_id || ''),
      })
      ElMessage.success(`自动私信已加入队列：${String(data?.id || '').slice(0, 8)}`)
      await refreshAllMessageData()
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || '自动私信失败')
    }
  }

  async function startBatch(payload: Dict) {
    if (String(payload.platform || '') !== 'dy') return ElMessage.info('AI一键私信已跳过快手/小红书平台，请选择抖音关键词')
    try {
      const { data } = await api.post('/message-workbench/auto-message-batches', payload)
      ElMessage.success(`自动私信批次 ${data.id} 已启动`)
      await loadBatches(String(data.id || ''))
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || '启动自动私信批次失败')
    }
  }

  async function cancelBatch(batch: Dict) {
    const batchId = batch?.id
    if (!batchId) return ElMessage.error('当前批次缺少ID，无法取消')
    try {
      await api.post(`/message-workbench/auto-message-batches/${batchId}/cancel`)
      ElMessage.success('已请求取消自动私信批次')
      await loadBatches(String(batchId))
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || '取消自动私信批次失败')
    }
  }

  async function retryBatch(batch: Dict) {
    const batchId = batch?.id
    if (!batchId) return ElMessage.error('当前批次缺少ID，无法重试')
    try {
      const { data } = await api.post(`/message-workbench/auto-message-batches/${batchId}/retry`)
      ElMessage.success(`已创建重试批次 ${data.id}`)
      await loadBatches(String(data.id || ''))
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || '重试自动私信批次失败')
    }
  }

  async function deleteBatch(batch: Dict) {
    const batchId = batch?.id
    if (!batchId) return ElMessage.error('当前批次缺少ID，无法删除')
    try {
      await ElMessageBox.confirm(`只删除自动私信批次 ${batchId} 的历史记录，不会删除客户数据。确认继续？`, '删除批次记录', { type: 'warning' })
      await api.delete(`/message-workbench/auto-message-batches/${batchId}`)
      ElMessage.success('自动私信批次记录已删除')
      await loadBatches('')
    } catch (error: any) {
      if (error === 'cancel' || error === 'close') return
      ElMessage.error(error?.response?.data?.detail || '删除自动私信批次失败')
    }
  }

  return {
    keywords,
    customers,
    detail,
    loading,
    filters,
    batches,
    load,
    loadBatches,
    changeFilter,
    selectCustomer,
    closeDetail,
    updateFollowStatus,
    messageCustomer,
    autoMessageCustomer,
    startBatch,
    cancelBatch,
    retryBatch,
    deleteBatch,
  }
}


export function selectDmScript(settings: Dict, aiScript: unknown) {
  // 固定话术优先使用当前设置，AI 模式使用客户分析结果。
  if (String(settings.dm_script_mode || 'ai') === 'fixed') {
    return {
      text: String(settings.fixed_dm_script || '').trim(),
      label: '固定话术',
      emptyMessage: '固定话术为空，请先到“私信设置”填写固定话术',
    }
  }
  return {
    text: String(aiScript || '').trim(),
    label: 'AI话术',
    emptyMessage: '当前客户暂无AI话术，请先做意向分析',
  }
}
