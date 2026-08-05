import { h } from 'vue'
import { ElMessageBox } from 'element-plus'

import { api } from './api'
import type { Dict } from './types'


const COUNT_LABELS: Dict = {
  accounts: '账号',
  author_account: '作者账号墓碑',
  comments: '评论',
  comment: '评论墓碑',
  contents: '内容',
  content: '内容墓碑',
  leads: '线索',
  analysis_jobs: 'AI任务',
}


// 批量操作统一先展示服务端计算的影响范围，再由用户确认执行。
export async function confirmBulkPreview(payload: Dict, title = '批量操作预览') {
  const { data } = await api.post('/bulk-actions/preview', payload)
  await ElMessageBox.confirm(renderMessage(data), title, {
    type: data.tombstone_counts && Object.keys(data.tombstone_counts).length ? 'warning' : 'info',
    confirmButtonText: '确认执行',
    cancelButtonText: '取消',
    customClass: 'bulk-preview-message-box',
  })
  return data
}

function renderMessage(data: Dict) {
  return h('div', { class: 'bulk-preview-message' }, [
    h('p', { class: 'bulk-preview-confirm' }, data.confirm_text || '确认执行当前批量操作？'),
    h('div', { class: 'bulk-preview-metrics' }, [
      metric('符合条件', data.eligible_count || 0, 'success'),
      metric('跳过', data.skipped_count || 0, 'muted'),
    ]),
    countSection('预计影响', data.affected_counts || {}),
    countSection('预计写入墓碑', data.tombstone_counts || {}),
    samples(data.sample_rows || []),
    warnings(data.warnings || []),
  ].filter(Boolean))
}

function metric(label: string, value: unknown, tone = '') {
  return h('div', { class: ['bulk-preview-metric', tone ? `is-${tone}` : ''] }, [
    h('small', label),
    h('strong', String(value)),
  ])
}

function countSection(title: string, counts: Dict) {
  const entries = Object.entries(counts).filter(([, value]) => Number(value) > 0)
  return h('section', { class: 'bulk-preview-section' }, [
    h('h4', title),
    entries.length
      ? h('div', { class: 'bulk-preview-counts' }, entries.map(([key, value]) => (
        h('span', { class: 'bulk-preview-count' }, [
          h('em', COUNT_LABELS[key] || key),
          h('strong', String(value)),
        ])
      )))
      : h('span', { class: 'bulk-preview-empty' }, '无'),
  ])
}

function samples(rows: Dict[]) {
  if (!rows.length) return null
  return h('section', { class: 'bulk-preview-section' }, [
    h('h4', '样例对象'),
    h('ul', { class: 'bulk-preview-samples' }, rows.map(row => h('li', [
      h('span', { title: String(row.name || row.id || '-') }, row.name || row.id || '-'),
      row.status ? h('em', row.status) : null,
    ]))),
  ])
}

function warnings(items: string[]) {
  if (!items.length) return null
  return h('section', { class: 'bulk-preview-warning' }, [
    h('h4', '注意'),
    h('ul', items.map(item => h('li', item))),
  ])
}
