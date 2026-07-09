import { defineComponent, h, onBeforeUnmount, onMounted, ref } from 'vue'
import { Clock, Delete, Refresh, RefreshRight, VideoPause } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { api } from '../../shared/api'
import type { Dict } from '../../shared/types'
import { sectionTitle } from '../ui/Workbench'


export const RuntimeQueuePanel = defineComponent({
  name: 'RuntimeQueuePanel',
  setup() {
    const queue = ref<Dict>({ items: [], total: 0, active: {} })
    const loading = ref(false)
    const operating = ref('')
    let timer = 0

    async function load(silent = false) {
      if (!silent) loading.value = true
      try {
        const { data } = await api.get('/runtime/jobs', { params: { page_size: 20 } })
        queue.value = data
      } catch (error: any) {
        if (!silent) ElMessage.error(error?.response?.data?.detail || '运行队列加载失败')
      } finally {
        if (!silent) loading.value = false
      }
    }

    async function cancel(job: Dict) {
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

    async function retry(job: Dict) {
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

    async function remove(job: Dict) {
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

    onMounted(() => {
      void load()
      timer = window.setInterval(() => void load(true), 3000)
    })
    onBeforeUnmount(() => window.clearInterval(timer))

    return () => {
      const items = queue.value.items || []
      const active = queue.value.active || {}
      const activeCount = Object.values(active).reduce((sum: number, value) => sum + Number(value || 0), 0)
      return h('section', { class: 'runtime-queue-panel' }, [
        h('div', { class: 'runtime-queue-heading' }, [
          sectionTitle({ title: '统一运行队列', subtitle: `${activeCount} 个执行中 · 共 ${queue.value.total || 0} 条`, icon: Clock, tone: activeCount ? 'green' : 'blue', compact: true }),
          h('button', { class: 'text-icon-button', disabled: loading.value, onClick: () => load() }, [h(Refresh, { class: 'inline-icon' }), loading.value ? '刷新中' : '刷新'])
        ]),
        items.length
          ? h('div', { class: 'runtime-job-list' }, items.map((job: Dict) => renderJob(job, operating.value, cancel, retry, remove)))
          : h('div', { class: 'diagnostic-empty' }, loading.value ? '正在读取队列...' : '当前没有运行队列记录')
      ])
    }
  }
})


function renderJob(
  job: Dict,
  operating: string,
  cancel: (job: Dict) => void,
  retry: (job: Dict) => void,
  remove: (job: Dict) => void
) {
  const status = String(job.status || '')
  const busy = operating === String(job.id || '')
  const active = ['queued', 'running'].includes(status)
  return h('article', { class: ['runtime-job-row', `runtime-${status}`] }, [
    h('div', { class: 'runtime-job-main' }, [
      h('div', [
        h('strong', runtimeKindLabel(String(job.kind || ''))),
        h('span', String(job.entity_id || '').slice(0, 24) || '无业务标识')
      ]),
      h('em', { class: `status ${status}` }, runtimeStatusLabel(status))
    ]),
    h('small', runtimeJobSummary(job)),
    h('div', { class: 'runtime-job-actions' }, [
      active ? h('button', { disabled: busy, onClick: () => cancel(job) }, [h(VideoPause, { class: 'inline-icon' }), '取消']) : null,
      ['failed', 'interrupted', 'cancelled'].includes(status) ? h('button', { disabled: busy, onClick: () => retry(job) }, [h(RefreshRight, { class: 'inline-icon' }), '重试']) : null,
      !active ? h('button', { disabled: busy, onClick: () => remove(job) }, [h(Delete, { class: 'inline-icon' }), '删除']) : null
    ])
  ])
}


function runtimeKindLabel(kind: string) {
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
    message_batch: '批量自动私信'
  } as Record<string, string>)[kind] || kind || '未知任务'
}


function runtimeStatusLabel(status: string) {
  return ({
    queued: '排队中',
    running: '执行中',
    succeeded: '已完成',
    failed: '失败',
    cancelled: '已取消',
    interrupted: '被中断'
  } as Record<string, string>)[status] || status || '未知'
}


function runtimeJobSummary(job: Dict) {
  const parts = [`资源：${job.resource === 'browser' ? '浏览器' : job.resource === 'ai' ? 'AI' : '后台'}`, `尝试：${job.attempt || 0}/${job.max_attempts || 1}`]
  if (job.error) parts.push(String(job.error))
  else if (job.started_at) parts.push(`开始：${job.started_at}`)
  else parts.push(`创建：${job.created_at || '-'}`)
  return parts.join(' · ')
}
