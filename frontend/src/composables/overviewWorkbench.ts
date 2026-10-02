import { ref, type Ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { api } from '../shared/api'
import { confirmBulkPreview } from '../shared/bulkPreview'
import { competitorStatusLabel, platformName } from '../shared/format'
import type { Dict } from '../shared/types'
import { selectDmScript } from './messageWorkbench'


type OverviewWorkbenchOptions = {
  settings: Ref<Dict>
  refreshTasks: () => Promise<unknown>
  refreshAi: () => Promise<unknown>
  refreshTable: () => Promise<unknown>
  openTaskLogs: (id: string) => Promise<unknown>
}


export function useOverviewWorkbench(options: OverviewWorkbenchOptions) {
  const tree = ref<Dict[]>([])

  async function load() {
    const { data } = await api.get('/overview/tree')
    tree.value = data
  }

  // 总览操作只声明需要联动的页面，树本身始终刷新。
  async function refresh(...loaders: Array<() => Promise<unknown>>) {
    await Promise.allSettled([load(), ...loaders.map(loader => loader())])
  }

  async function analyzeAccount(node: Dict) {
    const accountId = node.metrics?.id
    if (!accountId) {
      ElMessage.error('当前账号缺少ID，无法分析')
      return
    }
    try {
      const { data } = await api.post(`/accounts/${accountId}/analysis`)
      ElMessage.success(`账号分析任务 ${data.id} 已创建，采集完成后会自动执行AI判断`)
      await refresh(options.refreshTasks, options.refreshAi)
      await options.openTaskLogs(data.id)
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || '账号分析任务创建失败')
    }
  }

  async function analyzeCustomerIntent(node: Dict) {
    const leadId = node.metrics?.lead_id || String(node.id || '').split(':')[1]
    if (!leadId) {
      ElMessage.error('当前客户缺少线索ID，无法意向分析')
      return
    }
    try {
      await api.post(`/overview/customers/${leadId}/intent-analysis`)
      ElMessage.success('客户意向分析完成')
      await refresh(options.refreshAi, options.refreshTable)
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || '客户意向分析失败')
    }
  }

  async function updateCustomerFollowStatus(node: Dict, status: string) {
    const leadId = node.metrics?.lead_id || node.metrics?.id || String(node.id || '').split(':')[1]
    if (!leadId) {
      ElMessage.error('当前客户缺少线索ID，无法修改跟进状态')
      return
    }
    const currentStatus = String(node.metrics?.follow_status)
    if (currentStatus === status) return
    try {
      if (['已成交', '未成交'].includes(currentStatus)) {
        await ElMessageBox.confirm(`当前客户已经是“${currentStatus}”，确认改为“${status}”？`, '修改跟进状态', { type: 'warning' })
      }
      const note = `人工修改跟进状态：${currentStatus} -> ${status}`
      await api.patch(`/overview/customers/${leadId}/follow-status`, { follow_status: status, note })
      ElMessage.success(`跟进状态已更新为“${status}”`)
      await refresh(options.refreshTable, options.refreshAi)
    } catch (error: any) {
      if (error === 'cancel' || error === 'close') return
      ElMessage.error(error?.response?.data?.detail || '跟进状态修改失败')
    }
  }

  async function messageCustomer(node: Dict) {
    const leadId = node.metrics?.lead_id || node.metrics?.id || String(node.id || '').split(':')[1]
    const scriptSelection = selectDmScript(options.settings.value, node.metrics?.script)
    const profileUrl = String(node.metrics?.profile_url || '').trim()
    if (!leadId) {
      ElMessage.error('当前客户缺少线索ID，无法标记私信')
      return
    }
    if (!scriptSelection.text) {
      ElMessage.error(scriptSelection.emptyMessage)
      return
    }
    if (!profileUrl) {
      ElMessage.error('当前客户缺少主页链接，无法打开主页')
      return
    }
    try {
      const homepage = window.open(profileUrl, '_blank')
      if (!homepage) {
        ElMessage.warning('浏览器拦截了主页窗口，未复制话术，也未修改跟进状态')
        return
      }
      homepage.opener = null
      await navigator.clipboard.writeText(scriptSelection.text)

      // AI 筛选与人工跟进分开，已进入后续阶段的客户不回退。
      const currentStatus = String(node.metrics?.follow_status)
      if (['待筛选', '未私信'].includes(currentStatus)) {
        await api.patch(`/overview/customers/${leadId}/follow-status`, {
          follow_status: '已私信',
          note: `点击私信按钮：复制${scriptSelection.label}并打开客户主页`,
          record_message_attempt: true,
        })
        ElMessage.success(`${scriptSelection.label}已复制，客户主页已打开，跟进状态已更新为“已私信”`)
      } else {
        ElMessage.success(`${scriptSelection.label}已复制，客户主页已打开；当前状态“${currentStatus}”未回退`)
      }
      await refresh(options.refreshTable, options.refreshAi)
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || '私信操作失败')
    }
  }

  async function analyzeAccountCustomers(node: Dict) {
    const accountId = node.metrics?.id
    if (!accountId) {
      ElMessage.error('当前竞品账号缺少ID，无法一键意向分析')
      return
    }
    try {
      const { data } = await api.post(`/overview/accounts/${accountId}/customers/analyze`)
      const runnableCount = Number(data.created || 0) + Number(data.resumed || 0)
      if (runnableCount) {
        ElMessage.success(`已创建 ${data.created || 0} 个、恢复 ${data.resumed || 0} 个客户意向分析任务，并行数 ${data.concurrency || 1}`)
      } else if (data.lead_count) {
        ElMessage.info(`客户账号暂无可启动的意向分析任务，跳过 ${data.skipped?.length || 0} 个运行中任务`)
      } else {
        ElMessage.info('当前竞品账号下没有可分析的客户账号')
      }
      await refresh(options.refreshAi, options.refreshTable)
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || '一键意向分析失败')
    }
  }

  async function analyzeKeywordCompetitors(node: Dict) {
    const { platform, keyword } = keywordScope(node)
    if (!platform || !keyword) {
      ElMessage.error('当前关键词缺少平台或关键词信息，无法分析')
      return
    }
    if (!['dy', 'xhs', 'ks'].includes(platform)) {
      ElMessage.error('当前平台不支持主页资料采集，无法批量账号分析')
      return
    }
    const pendingCount = (node.children || []).filter((child: Dict) => competitorStatusLabel(child.metrics?.competitor_status) === '未分析').length
    try {
      await confirmBulkPreview({ action: 'keyword_analyze', target_type: 'keyword', filters: { platform, keyword } }, '一键竞品分析预览')
      const { data } = await api.post('/overview/keywords/analyze', null, { params: { platform, keyword } })
      if (data.created) ElMessage.success(`已创建 1 个账号分析任务，包含 ${data.account_count || 0} 个未分析账号`)
      else if (pendingCount) ElMessage.info('未分析账号已有运行中的账号分析任务')
      else ElMessage.info('当前关键词下没有未分析账号')
      await refresh(options.refreshTasks, options.refreshAi)
      if (data.task_ids?.length) await options.openTaskLogs(data.task_ids[0])
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || '一键竞品分析失败')
    }
  }

  async function deleteAccountNonCustomers(node: Dict) {
    const accountId = node.metrics?.id
    if (!accountId) {
      ElMessage.error('当前账号缺少ID，无法删除非客户')
      return
    }
    try {
      await confirmBulkPreview({ action: 'delete_non_customers', target_type: 'lead', filters: { source_account_id: accountId } }, '删除非客户预览')
      const { data } = await api.post(`/overview/accounts/${accountId}/customers/non-customers/delete`)
      if (data.deleted) ElMessage.success(`已删除 ${data.deleted} 个非客户`)
      else ElMessage.info('当前账号下没有可删除的非客户')
      if (data.failed) ElMessage.warning(`有 ${data.failed} 个非客户删除失败，请查看返回错误`)
      await refresh(options.refreshTable, options.refreshTasks, options.refreshAi)
    } catch (error: any) {
      if (error === 'cancel' || error === 'close') return
      ElMessage.error(error?.response?.data?.detail || '删除非客户失败')
    }
  }

  async function deleteAccount(node: Dict) {
    const accountId = node.metrics?.id
    if (!accountId) {
      ElMessage.error('当前账号缺少ID，无法删除')
      return
    }
    try {
      await ElMessageBox.confirm('此操作会删除该账号相关内容、评论、线索来源和可清理账号，并同步清理原始采集映射。确认删除该账号？', '删除账号确认', { type: 'warning' })
      const { data } = await api.delete(`/overview/accounts/${accountId}`)
      ElMessage.success(deleteMessage('账号数据已删除', data))
      await refresh(options.refreshTable, options.refreshTasks)
    } catch (error: any) {
      if (error === 'cancel' || error === 'close') return
      ElMessage.error(error?.response?.data?.detail || '账号删除失败')
    }
  }

  async function deleteCustomer(node: Dict) {
    const leadId = node.metrics?.id
    if (!leadId) {
      ElMessage.error('当前客户缺少线索ID，无法删除')
      return
    }
    try {
      await ElMessageBox.confirm('此操作会删除该客户账号、评论证据和相关线索来源，并记录防重复墓碑。确认删除该客户？', '删除客户确认', { type: 'warning' })
      const sourceAccountId = node.metrics?.source_account_id
      const { data } = await api.delete(`/overview/customers/${leadId}`, {
        params: sourceAccountId ? { source_account_id: sourceAccountId } : {},
      })
      ElMessage.success(deleteMessage('客户数据已删除', data))
      await refresh(options.refreshTable, options.refreshTasks)
    } catch (error: any) {
      if (error === 'cancel' || error === 'close') return
      ElMessage.error(error?.response?.data?.detail || '客户删除失败')
    }
  }

  async function deleteKeywordNonCompetitors(node: Dict) {
    const { platform, keyword } = keywordScope(node)
    if (!platform || !keyword) {
      ElMessage.error('当前关键词缺少平台或关键词信息，无法删除')
      return
    }
    try {
      await confirmBulkPreview({ action: 'delete_non_competitors', target_type: 'competitor', filters: { platform, keyword } }, '一键删除非竞品预览')
      const { data } = await api.post('/overview/keywords/non-competitors/delete', null, { params: { platform, keyword } })
      if (data.deleted) ElMessage.success(`已删除 ${data.deleted} 个非竞品账号`)
      else ElMessage.info('当前关键词下没有可删除的非竞品账号')
      await refresh(options.refreshTable)
    } catch (error: any) {
      if (error === 'cancel' || error === 'close') return
      ElMessage.error(error?.response?.data?.detail || '一键删除非竞品失败')
    }
  }

  async function deletePlatform(node: Dict) {
    const platform = node.metrics?.platform || node.label
    if (!platform) {
      ElMessage.error('当前平台缺少平台信息，无法删除')
      return
    }
    try {
      await ElMessageBox.confirm(`将硬删除“${platformName(platform)}”平台下的内容、评论、线索、账号来源，并同步清理原始采集映射。确认继续？`, '删除平台数据', { type: 'warning' })
      const { data } = await api.delete(`/overview/platforms/${encodeURIComponent(platform)}`)
      ElMessage.success(deleteMessage('平台数据已删除', data))
      await refresh(options.refreshTable, options.refreshTasks)
    } catch (error: any) {
      if (error === 'cancel' || error === 'close') return
      ElMessage.error(error?.response?.data?.detail || '平台数据删除失败')
    }
  }

  async function deleteKeyword(node: Dict) {
    const { platform, keyword } = keywordScope(node)
    if (!platform || !keyword) {
      ElMessage.error('当前关键词缺少平台或关键词信息，无法删除')
      return
    }
    try {
      await ElMessageBox.confirm(`将硬删除“${platformName(platform)} / ${keyword}”关键词下的内容、评论、线索、账号来源，并同步清理原始采集映射。确认继续？`, '删除关键词数据', { type: 'warning' })
      const { data } = await api.delete('/overview/keywords', { params: { platform, keyword } })
      ElMessage.success(deleteMessage('关键词数据已删除', data))
      await refresh(options.refreshTable, options.refreshTasks)
    } catch (error: any) {
      if (error === 'cancel' || error === 'close') return
      ElMessage.error(error?.response?.data?.detail || '关键词数据删除失败')
    }
  }

  async function findCustomers(target: Dict) {
    try {
      let data: Dict
      if (target.kind === 'keyword') {
        const { platform, keyword } = keywordScope(target)
        if (!platform || !keyword) {
          ElMessage.error('当前关键词缺少平台或关键词信息，无法找客户')
          return
        }
        await confirmBulkPreview({ action: 'keyword_find_customers', target_type: 'keyword', filters: { platform, keyword } }, '一键找客户预览')
        data = (await api.post('/overview/keywords/find-customers', null, { params: { platform, keyword } })).data
      } else {
        const accountId = target.metrics?.id || target.id
        if (!accountId) {
          ElMessage.error('当前账号缺少ID，无法找客户')
          return
        }
        data = (await api.post(`/accounts/${accountId}/find-customers`)).data
      }

      if (data.created) {
        const taskCount = data.task_ids?.length || data.created || 0
        ElMessage.success(`已创建 ${taskCount} 个找客户任务：复用已有内容 ${data.reuse_content_count || 0} 条，补采账号 ${data.creator_account_count || 0} 个，新内容目标 ${data.supplement_content_count || 0} 条，跳过已采内容 ${data.skip_content_count || 0} 条，近期评论跳过 ${data.recent_comment_skip_count || 0} 条`)
      } else if (data.recent_comment_skip_count) {
        ElMessage.info(`近期已采过 ${data.recent_comment_skip_count} 条内容的评论，未重复创建采集任务`)
      } else {
        ElMessage.info(data.skipped?.length ? '相关账号已有运行中的找客户任务' : '没有可用于找客户的账号')
      }
      await refresh(options.refreshTasks, options.refreshAi)
      if (data.task_ids?.length) await options.openTaskLogs(data.task_ids[0])
    } catch (error: any) {
      ElMessage.error(error?.response?.data?.detail || '找客户任务创建失败')
    }
  }

  return {
    tree,
    load,
    analyzeAccount,
    analyzeCustomerIntent,
    updateCustomerFollowStatus,
    messageCustomer,
    analyzeAccountCustomers,
    analyzeKeywordCompetitors,
    deleteAccountNonCustomers,
    deleteAccount,
    deleteCustomer,
    deleteKeywordNonCompetitors,
    deletePlatform,
    deleteKeyword,
    findCustomers,
  }
}

function keywordScope(node: Dict) {
  const idParts = String(node.id || '').split(':')
  const idPlatform = idParts[0] === 'keyword' ? idParts[1] || '' : ''
  const idKeyword = idParts[0] === 'keyword' ? idParts.slice(2).join(':') : ''
  return {
    platform: String(node.metrics?.platform || idPlatform || '').trim(),
    keyword: String(node.metrics?.keyword || node.label || idKeyword || '').trim(),
  }
}

function deleteMessage(prefix: string, data: Dict) {
  const counts = data.counts || {}
  return `${prefix}：内容 ${counts.contents || 0} / 评论 ${counts.comments || 0} / 账号 ${counts.accounts || 0} / 线索 ${counts.leads || 0}`
}
