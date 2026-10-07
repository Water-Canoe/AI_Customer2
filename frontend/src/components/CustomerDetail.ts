import { h } from 'vue'
import { ElMessage } from 'element-plus'
import { Close } from '@element-plus/icons-vue'
import type { Dict } from '../shared/types'
import { COMPACT_PAGE_SIZE, ListPagination, paginateItems, type PageChange } from './ui/ListPagination'

// 客户跟进与私信收件箱共用详情内容，抽屉开关由各页面管理。
export function renderCustomerDetail(detail: Dict, emit: any, pagination: Dict) {
  if (!detail?.customer) return null
  const customer = detail.customer || {}
  const sources = (detail.sources || []) as Dict[]
  const events = (detail.events || []) as Dict[]
  const sourcePagination = paginateItems(sources, pagination.sourcePage, COMPACT_PAGE_SIZE)
  const eventPagination = paginateItems(events, pagination.eventPage, COMPACT_PAGE_SIZE)
  const rawScript = String(customer.script || '').trim()
  const script = rawScript || '暂无AI话术'
  return h('div', { class: 'message-detail-drawer' }, [
    h('div', { class: 'drawer-head' }, [
      h('div', [
        h('h3', customer.nickname || '客户详情'),
        h('p', customer.keyword_text || '无关键词')
      ]),
      h('button', { type: 'button', class: 'icon-button', title: '关闭详情', 'aria-label': '关闭详情', onClick: () => emit('close-detail') }, [h(Close)])
    ]),
    ['dy', 'xhs'].includes(customer.platform) && pagination.openConversation ? h('div', { class: 'toolbar operations-next-actions' }, [h('button', {
      class: 'secondary-action', disabled: !pagination.accountId || pagination.openingConversation,
      title: pagination.accountId ? '使用当前私信账号打开聊天窗口' : '请先配置客户平台对应的私信账号',
      onClick: () => pagination.openConversation(customer),
    }, pagination.openingConversation ? '打开中…' : '打开私信会话')]) : null,
    h('div', {
      class: ['drawer-section', 'drawer-script-section', rawScript ? 'copyable-script' : 'empty-script'],
      title: rawScript ? `${rawScript}\n点击复制AI话术` : script,
      role: rawScript ? 'button' : undefined,
      tabindex: rawScript ? 0 : undefined,
      onClick: rawScript ? (event: MouseEvent) => copyAiScript(rawScript, event) : undefined,
      onKeydown: rawScript ? (event: KeyboardEvent) => {
        if (event.key === 'Enter' || event.key === ' ') copyAiScript(rawScript, event)
      } : undefined
    }, [
      h('h4', rawScript ? 'AI话术 · 点击复制' : 'AI话术'),
      h('p', script)
    ]),
    h('div', { class: 'drawer-section' }, [
      h('h4', 'AI分析原因'),
      h('p', customer.reason || '暂无AI分析原因')
    ]),
    h('div', { class: 'tabs' }, ['sources', 'events'].map(tab => h('button', { class: pagination.detailTab === tab ? 'active' : '', onClick: () => pagination.setDetailTab(tab) }, tab === 'sources' ? `来源证据 (${sources.length})` : `跟进历史 (${events.length})`))),
    pagination.detailTab === 'sources' ? h('div', { class: 'drawer-section' }, [
      h('h4', '来源证据'),
      ...sourcePagination.items.map((source: Dict) => h('article', { class: 'source-card' }, [
        h('strong', source.keyword || '未标记关键词'),
        h('p', source.comment_text || '无评论内容'),
        h('small', source.video_text || '无视频详情'),
        source.content_url ? h('a', { href: source.content_url, target: '_blank', rel: 'noreferrer' }, '打开视频') : null,
        source.source_account_name ? h('span', `来源账号：${source.source_account_name}`) : null
      ])),
      h(ListPagination, {
        page: sourcePagination.page,
        pageSize: sourcePagination.pageSize,
        total: sourcePagination.total,
        compact: true,
        onChange: (payload: PageChange) => pagination.setSourcePage(payload.page),
      }),
    ]) : null,
    pagination.detailTab === 'events' ? h('div', { class: 'drawer-section' }, [
      h('h4', '跟进时间线'),
      events.length
        ? h('ol', { class: 'event-timeline' }, eventPagination.items.map((event: Dict) => h('li', [
            h('strong', `${event.from_status || '-'} -> ${event.to_status || '-'}`),
            h('span', event.created_at || ''),
            event.note ? h('p', event.note) : null
          ])))
        : h('p', '暂无人工跟进事件'),
      h(ListPagination, {
        page: eventPagination.page,
        pageSize: eventPagination.pageSize,
        total: eventPagination.total,
        compact: true,
        onChange: (payload: PageChange) => pagination.setEventPage(payload.page),
      }),
    ]) : null
  ])
}


// 详情和客户表格共用复制行为，空话术不会写入剪贴板。
export async function copyAiScript(value: unknown, event?: MouseEvent | KeyboardEvent) {
  event?.stopPropagation()
  if (event instanceof KeyboardEvent) event.preventDefault()
  const text = String(value || '').trim()
  if (!text) {
    ElMessage.warning('暂无AI话术可复制')
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('AI话术已复制')
  } catch {
    ElMessage.error('复制失败，请检查浏览器剪贴板权限')
  }
}

