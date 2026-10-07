import { defineComponent, h, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElDrawer } from 'element-plus'
import { Tickets, User } from '@element-plus/icons-vue'

import '../styles/operations.css'
import { emptyState, sectionTitle } from '../components/ui/Workbench'
import { platformName } from '../shared/format'
import type { Dict } from '../shared/types'
import { ListPagination, type PageChange } from '../components/ui/ListPagination'


export default defineComponent({
  props: {
    batches: { type: Object, default: () => ({ batches: [], active: null, selected_batch_id: '', items: [] }) },
  },
  emits: ['select-auto-message-batch', 'cancel-auto-message-batch', 'retry-auto-message-batch', 'delete-auto-message-batch'],
  setup(props, { emit }) {
    const route = useRoute()
    const router = useRouter()
    const detailOpen = ref(Boolean(route.query.batch))
    watch(() => [route.name, route.query] as const, ([name, query]) => {
      if (name !== 'message-batches') return
      detailOpen.value = Boolean(query.batch)
      // 明细翻页不改变批次 ID，也必须跟随地址恢复列表和明细位置。
      const id = String(query.batch || '')
      const pagination = { page: Number(query.page || 1), page_size: Number(query.page_size || 10), item_page: Number(query.item_page || 1), item_page_size: Number(query.item_page_size || 20) }
      if ((id && String(props.batches.selected_batch_id || '') !== id) || Object.entries(pagination).some(([key, value]) => Number(props.batches[key] || 0) !== value)) emit('select-auto-message-batch', { id, ...pagination })
    }, { immediate: true })
    function batchQuery(id: string, itemPage = Number(props.batches.item_page || 1)) {
      return { batch: id, page: Number(props.batches.page || 1), page_size: Number(props.batches.page_size || 10), item_page: itemPage, item_page_size: Number(props.batches.item_page_size || 20) }
    }
    function select(batch: Dict) {
      detailOpen.value = true
      void router.replace({ path: '/message-batches', query: batchQuery(String(batch.id), 1) })
    }
    return () => {
      const data = props.batches as Dict
      const batchList = Array.isArray(data.batches) ? data.batches as Dict[] : []
      const selected = resolveSelectedMessageBatch(data)
      const items = Array.isArray(data.items) ? data.items as Dict[] : []

      return h('div', { class: 'workspace-page operations-page' }, [
        h('section', { class: 'panel' }, [
          sectionTitle({ title: '触达记录', subtitle: `共 ${Number(data.total || 0)} 个自动私信批次`, icon: Tickets, tone: 'purple' }),
          batchList.length ? h('div', { class: 'operations-list' }, batchList.map(batch => renderBatchHistoryCard(batch, String(selected?.id || '') === String(batch.id || ''), () => select(batch)))) : emptyState({ title: '还没有触达记录', description: '启动自动私信后，在这里查看每位客户的执行结果。', icon: Tickets }),
          h(ListPagination, { page: Number(data.page || 1), pageSize: Number(data.page_size || 10), total: Number(data.total || 0), onChange: (payload: PageChange) => router.replace({ query: { page: payload.page, page_size: payload.page_size } }) }),
        ]),
        h(ElDrawer, { modelValue: detailOpen.value, title: '批次执行明细', size: '840px', class: 'operations-detail', 'onUpdate:modelValue': (value: boolean) => { if (!value) { detailOpen.value = false; void router.replace({ path: '/message-batches', query: { page: Number(data.page || 1), page_size: Number(data.page_size || 10) } }) } } }, () => selected ? renderBatchDetail(selected, items, data, {
          cancel: (batch: Dict) => emit('cancel-auto-message-batch', batch), retry: (batch: Dict) => emit('retry-auto-message-batch', batch), remove: (batch: Dict) => emit('delete-auto-message-batch', batch), changeItemPage: (payload: PageChange) => router.replace({ query: { ...batchQuery(String(selected.id), payload.page), item_page_size: payload.page_size } }),
          openCustomer: (item: Dict) => {
            const returnQuery = new URLSearchParams(Object.fromEntries(Object.entries(batchQuery(String(selected.id))).map(([key, value]) => [key, String(value)])))
            void router.push({ path: '/message-workbench', query: { customer: String(item.lead_account_id), status: '全部', return_to: `/message-batches?${returnQuery}` } })
          },
        }) : h('p', '正在加载批次详情…')),
      ])
    }
  },
})


export function resolveSelectedMessageBatch(data: Dict): Dict | null {
  // 以接口选择项为准；首次进入时回退到活动批次或最新批次。
  const batchList = Array.isArray(data.batches) ? data.batches as Dict[] : []
  const selectedId = String(data.selected_batch_id || '')
  return (data.selected_batch as Dict | null)
    || batchList.find(batch => String(batch.id || '') === selectedId)
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
      h('span', `失败/待确认 ${batch.failed_count || 0}`),
      h('span', `跳过 ${batch.skipped_count || 0}`),
    ]),
    h('small', batch.created_at || '-'),
  ])
}


function renderBatchDetail(batch: Dict, items: Dict[], pagination: Dict, actions: Dict) {
  const active = ['pending', 'queued', 'running'].includes(String(batch.status || ''))
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
      renderBatchMetric('失败/待确认', batch.failed_count || 0),
      renderBatchMetric('已跳过', batch.skipped_count || 0),
    ]),
    h('section', { class: 'message-batch-meta-panel' }, [
      renderBatchMeta('执行账号', batch.account_name ? `${batch.account_name} (${batch.account_id})` : batch.account_id || '未记录执行账号'),
      renderBatchMeta('创建时间', batch.created_at || '-'),
      renderBatchMeta('开始时间', batch.started_at || '-'),
      renderBatchMeta('结束时间', batch.finished_at || '-'),
      renderBatchMeta('发送间隔', `${batch.interval_min_seconds || 0} - ${batch.interval_max_seconds || 0} 秒`),
      batch.error ? h('p', { class: 'message-batch-error' }, batch.error) : null,
    ]),
    h('section', { class: 'message-batch-items-panel' }, [
      sectionTitle({ title: '客户执行明细', subtitle: `共 ${Number(pagination.item_total || 0)} 条`, icon: User, tone: 'blue' }),
      items.length
        ? h('div', { class: 'message-batch-item-detail-list' }, items.map(item => renderBatchItem(item, actions.openCustomer)))
        : emptyState({
          title: '当前批次没有执行明细',
          description: '批次刚创建时请稍候刷新；历史批次没有客户时不会产生明细。',
          icon: User,
          tone: 'gray',
        }),
      h(ListPagination, {
        page: Number(pagination.item_page || 1),
        pageSize: Number(pagination.item_page_size || 20),
        total: Number(pagination.item_total || 0),
        onChange: actions.changeItemPage,
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


function renderBatchItem(item: Dict, openCustomer: (item: Dict) => void) {
  return h('article', { class: 'message-batch-item-detail' }, [
    h('div', { class: 'message-batch-item-head' }, [
      item.profile_url
        ? h('a', { href: item.profile_url, target: '_blank', rel: 'noreferrer' }, item.nickname || `客户 ${item.lead_account_id}`)
        : h('strong', item.nickname || `客户 ${item.lead_account_id}`),
      renderBatchStatus(item.status),
      item.lead_account_id ? h('button', { class: 'text-icon-button', onClick: () => openCustomer(item) }, '查看客户跟进') : null,
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
    queued: '排队中',
    interrupted: '已中断',
    running: '执行中',
    succeeded: '成功',
    failed: '失败',
    uncertain: '结果待确认',
    skipped: '跳过',
    cancelled: '已取消',
    quota_reached: '额度已用完',
  } as Record<string, string>)[String(status || '')] || String(status || '-')
}
