import { defineComponent, h, onMounted, reactive, ref } from 'vue'
import { DataLine, Refresh, Search } from '@element-plus/icons-vue'

import { emptyState, pageAction, sectionTitle } from '../components/ui/Workbench'
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

    return () => h('section', { class: 'traffic-page' }, [
      pageAction({
        title: '引流数据表',
        description: '按评论成功记录展示已评论视频，用于观察防重复账本。',
        icon: DataLine,
        tone: 'teal',
        aside: h('button', { class: 'secondary-action', disabled: loading.value, onClick: () => load(records.value.page || 1) }, [h(Refresh, { class: 'inline-icon' }), '刷新']),
      }),
      h('section', { class: 'traffic-panel' }, [
        sectionTitle({ title: '已评论视频', subtitle: `共 ${records.value.total || 0} 条`, icon: DataLine, tone: 'blue', compact: true }),
        h('div', { class: 'traffic-record-toolbar' }, [
          h('input', {
            value: filters.query,
            placeholder: '搜索视频简介、评论内容或作者',
            onInput: (event: Event) => filters.query = (event.target as HTMLInputElement).value,
            onKeydown: (event: KeyboardEvent) => { if (event.key === 'Enter') void load(1) },
          }),
          h('button', { class: 'secondary-action', disabled: loading.value, onClick: () => load(1) }, [h(Search, { class: 'inline-icon' }), '搜索']),
        ]),
        renderRecords(records.value, load),
      ]),
    ])
  }
})

function renderRecords(records: Dict, load: (page?: number) => Promise<void>) {
  const rows = records.rows || []
  if (!rows.length) {
    return emptyState({ title: '暂无评论记录', description: '执行成功评论后会在这里显示。', icon: DataLine, tone: 'gray' })
  }
  const page = Number(records.page || 1)
  const pageSize = Number(records.page_size || 30)
  const start = (page - 1) * pageSize + 1
  const end = Math.min(page * pageSize, Number(records.total || 0))
  const hasNext = end < Number(records.total || 0)
  return h('div', { class: 'traffic-record-table' }, [
    h('div', { class: 'traffic-record-head' }, [
      h('span', '视频简介'),
      h('span', '评论内容'),
      h('span', '作者'),
      h('span', '点赞'),
      h('span', '时间'),
    ]),
    ...rows.map((row: Dict) => h('div', { class: 'traffic-record-row' }, [
      h('a', { href: row.content_url || undefined, target: '_blank', rel: 'noreferrer' }, row.video_intro || row.content_url || row.target_key),
      h('span', row.comment_text || '-'),
      h('span', row.author_name || '-'),
      h('span', String(row.like_count || 0)),
      h('span', row.commented_at || row.created_at || '-'),
    ])),
    h('div', { class: 'traffic-record-footer' }, [
      h('span', `显示 ${start}-${end} / ${records.total || 0}`),
      h('div', { class: 'traffic-record-pager' }, [
        h('button', { class: 'secondary-action', disabled: page <= 1, onClick: () => load(page - 1) }, '上一页'),
        h('button', { class: 'secondary-action', disabled: !hasNext, onClick: () => load(page + 1) }, '下一页'),
      ]),
    ]),
  ])
}
