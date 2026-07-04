import { defineComponent, h, onMounted, reactive, ref } from 'vue'
import { DataLine, Refresh, Search } from '@element-plus/icons-vue'

import { iconBadge, sectionTitle } from '../components/ui/Workbench'
import { api } from '../shared/api'
import type { Dict } from '../shared/types'

// 数据表只展示已确认成功的评论记录；执行中的申领记录只用于防重复。
export default defineComponent({
  name: 'TrafficRecordsPage',
  setup() {
    const loading = ref(false)
    const records = ref<Dict>({ rows: [], total: 0, page: 1, page_size: 30 })
    const filters = reactive({ query: '' })

    async function load(page = 1) {
      loading.value = true
      try {
        const { data } = await api.get('/traffic/comment-records', {
          params: { page, page_size: records.value.page_size || 30, query: filters.query },
        })
        records.value = data
      } finally {
        loading.value = false
      }
    }

    onMounted(() => load())

    return () => h('section', { class: 'pane table-workspace' }, [
      h('div', { class: 'table-library-bar' }, [
        h('div', { class: 'library-list' }, [
          h('button', { class: 'selected' }, [
            iconBadge(DataLine, 'teal'),
            h('span', '已评论视频'),
          ]),
        ]),
        h('div', { class: 'table-filters traffic-record-filters' }, [
          h('input', {
            value: filters.query,
            placeholder: '搜索视频简介、评论内容或作者',
            onInput: (event: Event) => filters.query = (event.target as HTMLInputElement).value,
            onKeydown: (event: KeyboardEvent) => { if (event.key === 'Enter') void load(1) },
          }),
          h('button', { type: 'button', disabled: loading.value, onClick: () => load(1) }, [h(Search, { class: 'inline-icon' }), '筛选']),
          h('button', { type: 'button', class: 'ghost-button', disabled: loading.value, onClick: () => load(records.value.page || 1) }, [h(Refresh, { class: 'inline-icon' }), '刷新']),
        ]),
      ]),
      h('div', { class: 'table-content' }, [
        sectionTitle({ title: '引流数据表', subtitle: records.value.total ? `${records.value.total || 0} 条记录` : '0 条记录', icon: DataLine, tone: 'blue' }),
        ...renderRecords(records.value, load),
      ]),
    ])
  }
})

function renderRecords(records: Dict, load: (page?: number) => Promise<void>) {
  const rows = records.rows || []
  const page = Number(records.page || 1)
  const pageSize = Number(records.page_size || 30)
  const start = (page - 1) * pageSize + 1
  const end = Math.min(page * pageSize, Number(records.total || 0))
  const hasNext = end < Number(records.total || 0)
  return [
    h('div', { class: 'table-scroll' }, [
      h('table', { class: 'data-table traffic-record-data-table' }, [
        h('thead', [h('tr', [
          h('th', '视频简介'),
          h('th', '评论内容'),
          h('th', '作者'),
          h('th', '点赞'),
          h('th', '时间'),
        ])]),
        h('tbody', rows.length ? rows.map((row: Dict) => h('tr', [
          h('td', [renderRecordTitle(row)]),
          h('td', [h('span', { class: 'table-muted-text' }, row.comment_text || '-')]),
          h('td', [h('span', { class: 'table-plain-text' }, row.author_name || '-')]),
          h('td', String(row.like_count || 0)),
          h('td', [h('span', { class: 'table-muted-text' }, row.commented_at || row.created_at || '-')]),
        ])) : [
          h('tr', [h('td', { class: 'table-empty', colspan: 5 }, '暂无评论记录；执行成功评论后会在这里显示。')]),
        ]),
      ]),
    ]),
    h('div', { class: 'table-pagination' }, [
      h('span', records.total ? `显示 ${start}-${end} / ${records.total || 0}` : '0 条记录'),
      h('div', { class: 'table-page-controls' }, [
        h('button', { type: 'button', disabled: page <= 1, onClick: () => load(page - 1) }, '上一页'),
        h('span', `${page} / ${Math.max(1, Math.ceil(Number(records.total || 0) / pageSize))}`),
        h('button', { type: 'button', disabled: !hasNext, onClick: () => load(page + 1) }, '下一页'),
      ]),
    ]),
  ]
}

function renderRecordTitle(row: Dict) {
  const label = row.video_intro || row.content_url || row.target_key || '-'
  const attrs = { class: 'table-primary-text', title: label }
  return row.content_url
    ? h('a', { ...attrs, class: 'table-primary-link table-primary-text', href: row.content_url, target: '_blank', rel: 'noreferrer' }, label)
    : h('span', attrs, label)
}
