import { defineComponent, h } from 'vue'
import { Tickets, User } from '@element-plus/icons-vue'

import { SplitPane } from '../components/ui/SplitPane'
import { emptyState, sectionTitle } from '../components/ui/Workbench'
import { platformName } from '../shared/format'
import type { Dict } from '../shared/types'


export default defineComponent({
  props: {
    batches: { type: Object, default: () => ({ batches: [], active: null, selected_batch_id: '', items: [] }) },
  },
  emits: ['select-auto-message-batch', 'cancel-auto-message-batch', 'retry-auto-message-batch', 'delete-auto-message-batch'],
  setup(props, { emit }) {
    return () => {
      const data = props.batches as Dict
      const batchList = Array.isArray(data.batches) ? data.batches as Dict[] : []
      const selected = resolveSelectedMessageBatch(data)
      const items = Array.isArray(data.items) ? data.items as Dict[] : []

      return h(SplitPane, { storageKey: 'message-batches', side: 'right', defaultSideWidth: 390 }, {
        default: () => h('section', { class: 'pane primary-pane message-batch-detail-pane' }, [
          selected
            ? renderBatchDetail(selected, items, {
              cancel: (batch: Dict) => emit('cancel-auto-message-batch', batch),
              retry: (batch: Dict) => emit('retry-auto-message-batch', batch),
              remove: (batch: Dict) => emit('delete-auto-message-batch', batch),
            })
            : emptyState({
              title: '暂无自动私信批次',
              description: '在“客户跟进”选择抖音关键词并启动 AI 一键私信后，执行记录会显示在这里。',
              icon: Tickets,
              tone: 'gray',
            }),
        ]),
        side: () => h('aside', { class: 'pane side-pane message-batch-history-pane' }, [
          sectionTitle({ title: '批次列表', subtitle: '最近 20 个批次', icon: Tickets, tone: 'purple' }),
          batchList.length
            ? h('div', { class: 'message-batch-history-list' }, batchList.map(batch => renderBatchHistoryCard(
              batch,
              String(selected?.id || '') === String(batch.id || ''),
              () => emit('select-auto-message-batch', batch),
            )))
            : emptyState({
              title: '还没有批次记录',
              description: '批次启动后会按时间倒序显示。',
              icon: Tickets,
              tone: 'gray',
            }),
        ]),
      })
    }
  },
})


export function resolveSelectedMessageBatch(data: Dict): Dict | null {
  // 以接口选择项为准；首次进入时回退到活动批次或最新批次。
  const batchList = Array.isArray(data.batches) ? data.batches as Dict[] : []
  const selectedId = String(data.selected_batch_id || '')
  return batchList.find(batch => String(batch.id || '') === selectedId)
    || (data.active as Dict | null)
    || batchList[0]
    || null
}


function renderBatchHistoryCard(batch: Dict, selected: boolean, select: () => void) {
  return h('article', {
    class: ['message-batch-history-card', { selected }],
    role: 'button',
    tabindex: 0,
    onClick: select,
    onKeydown: (event: KeyboardEvent) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        select()
      }
    },
  }, [
    h('div', { class: 'message-batch-card-head' }, [
      h('strong', `批次 ${batch.id || '-'}`),
      renderBatchStatus(batch.status),
    ]),
    h('span', { class: 'message-batch-card-source' }, `${platformName(String(batch.platform || ''))} · ${batch.keyword || '未指定关键词'}`),
    h('div', { class: 'message-batch-card-metrics' }, [
      h('span', `目标 ${batch.total_count || 0}`),
      h('span', `成功 ${batch.success_count || 0}`),
      h('span', `失败 ${batch.failed_count || 0}`),
      h('span', `跳过 ${batch.skipped_count || 0}`),
    ]),
    h('small', batch.created_at || '-'),
  ])
}


function renderBatchDetail(batch: Dict, items: Dict[], actions: Dict) {
  const active = ['pending', 'running'].includes(String(batch.status || ''))
  const retryable = !active && Number(batch.failed_count || 0) + Number(batch.skipped_count || 0) > 0
  return [
    sectionTitle({
      title: `批次 ${batch.id || '-'}`,
      subtitle: `${platformName(String(batch.platform || ''))} · ${batch.keyword || '未指定关键词'}`,
      icon: Tickets,
      tone: 'purple',
      aside: h('div', { class: 'message-batch-detail-actions' }, [
        active ? h('button', { type: 'button', class: 'warning-soft', onClick: () => actions.cancel(batch) }, '取消批次') : null,
        retryable ? h('button', { type: 'button', class: 'primary-soft', onClick: () => actions.retry(batch) }, '重试失败项') : null,
        !active ? h('button', { type: 'button', class: 'danger', onClick: () => actions.remove(batch) }, '删除记录') : null,
      ]),
    }),
    h('div', { class: 'message-batch-summary-grid' }, [
      renderBatchMetric('状态', batchStatusLabel(String(batch.status || ''))),
      renderBatchMetric('目标客户', batch.total_count || 0),
      renderBatchMetric('发送成功', batch.success_count || 0),
      renderBatchMetric('发送失败', batch.failed_count || 0),
      renderBatchMetric('已跳过', batch.skipped_count || 0),
    ]),
    h('section', { class: 'message-batch-meta-panel' }, [
      renderBatchMeta('创建时间', batch.created_at || '-'),
      renderBatchMeta('开始时间', batch.started_at || '-'),
      renderBatchMeta('结束时间', batch.finished_at || '-'),
      renderBatchMeta('发送间隔', `${batch.interval_min_seconds || 0} - ${batch.interval_max_seconds || 0} 秒`),
      batch.error ? h('p', { class: 'message-batch-error' }, batch.error) : null,
    ]),
    h('section', { class: 'message-batch-items-panel' }, [
      sectionTitle({ title: '客户执行明细', subtitle: `${items.length} 条`, icon: User, tone: 'blue' }),
      items.length
        ? h('div', { class: 'message-batch-item-detail-list' }, items.map(renderBatchItem))
        : emptyState({
          title: '当前批次没有执行明细',
          description: '批次刚创建时请稍候刷新；历史批次没有客户时不会产生明细。',
          icon: User,
          tone: 'gray',
        }),
    ]),
  ]
}


function renderBatchMetric(label: string, value: unknown) {
  return h('div', [h('small', label), h('strong', String(value))])
}


function renderBatchMeta(label: string, value: unknown) {
  return h('div', [h('small', label), h('span', String(value))])
}


function renderBatchItem(item: Dict) {
  return h('article', { class: 'message-batch-item-detail' }, [
    h('div', { class: 'message-batch-item-head' }, [
      item.profile_url
        ? h('a', { href: item.profile_url, target: '_blank', rel: 'noreferrer' }, item.nickname || `客户 ${item.lead_account_id}`)
        : h('strong', item.nickname || `客户 ${item.lead_account_id}`),
      renderBatchStatus(item.status),
    ]),
    item.script ? h('p', { class: 'message-batch-item-script' }, item.script) : null,
    item.error ? h('p', { class: 'message-batch-item-error' }, item.error) : null,
    h('small', item.finished_at || item.started_at || item.created_at || '-'),
  ])
}


function renderBatchStatus(status: unknown) {
  const value = String(status || 'pending')
  return h('span', { class: `batch-status is-${value}` }, batchStatusLabel(value))
}


export function batchStatusLabel(status: string) {
  return ({
    pending: '排队中',
    running: '执行中',
    succeeded: '成功',
    failed: '失败',
    skipped: '跳过',
    cancelled: '已取消',
    quota_reached: '额度已用完',
  } as Record<string, string>)[String(status || '')] || String(status || '-')
}
