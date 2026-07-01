import { defineComponent, h, reactive, watch } from 'vue'
import { Check, Delete, Refresh } from '@element-plus/icons-vue'
import type { Dict } from '../shared/types'
import { SplitPane } from '../components/ui/SplitPane'

const icpFields = [
  { key: 'product', label: '产品/服务', placeholder: '例如：AI客服、获客工具' },
  { key: 'industry', label: '目标行业', placeholder: '例如：跨境电商、教育培训' },
  { key: 'roles', label: '目标角色', placeholder: '例如：老板、运营负责人、销售主管' },
  { key: 'pain_points', label: '典型痛点', placeholder: '用户常见问题、需求或抱怨', multiline: true },
  { key: 'high_intent_words', label: '高意向词', placeholder: '例如：求推荐、怎么选、多少钱', multiline: true },
  { key: 'value_proposition', label: '价值主张', placeholder: '产品能解决什么问题，适合什么客户', multiline: true },
  { key: 'excluded_audience', label: '排除人群', placeholder: '不需要跟进的人群或场景', multiline: true }
]

const commentCutoffOptions = [
  { value: 5, label: '5天内' },
  { value: 10, label: '10天内' },
  { value: 30, label: '30天内' },
  { value: 60, label: '60天内' },
  { value: 90, label: '90天内' },
  { value: 180, label: '180天内' },
  { value: 365, label: '365天内' },
  { value: 0, label: '不限' }
]

function defaultIcpProfile() {
  return Object.fromEntries(icpFields.map(field => [field.key, '']))
}

function normalizeIcpProfile(value: any) {
  let source = value
  if (typeof value === 'string') {
    try { source = JSON.parse(value) } catch { source = {} }
  }
  return { ...defaultIcpProfile(), ...(source && typeof source === 'object' ? source : {}) }
}

function buildIcpPayload(value: Dict) {
  const payload = defaultIcpProfile()
  Object.keys(payload).forEach(key => {
    payload[key] = String(value[key] || '').trim()
  })
  return payload
}

export default defineComponent({
  props: {
    settings: { type: Object, required: true },
    env: { type: Object, required: true },
    tombstoneSummary: { type: Object, default: () => ({}) },
    tombstones: { type: Object, default: () => ({ items: [] }) },
    tombstoneFilters: { type: Object, default: () => ({}) }
  },
  emits: ['save', 'check-env', 'clear-data', 'load-tombstones'],
  setup(props, { emit }) {
    const local = reactive<Dict>({})
    function sync() {
      Object.keys(local).forEach(key => delete local[key])
      Object.assign(local, JSON.parse(JSON.stringify(props.settings || {})))
      local.icp_profile = normalizeIcpProfile(local.icp_profile)
    }
    watch(() => props.settings, sync, { immediate: true, deep: true })
    return () => {
      if (!local.icp_profile || typeof local.icp_profile !== 'object') {
        local.icp_profile = normalizeIcpProfile(local.icp_profile)
      }
      const icpProfile = local.icp_profile as Dict
      return h(SplitPane, { storageKey: 'settings', side: 'right', defaultSideWidth: 360 }, {
        default: () => [
        h('section', { class: 'pane primary-pane' }, [
          h('div', { class: 'section-title' }, [h('h2', '基础配置'), h('span', '没有配置时 AI 分析会明确失败')]),
          h('div', { class: 'form-grid' }, [
            inputField(local, 'media_crawler_path', 'MediaCrawler路径'),
            inputField(local, 'media_crawler_db_path', '底层SQLite路径'),
            inputField(local, 'ai_base_url', 'AI Base URL'),
            inputField(local, 'ai_api_key', 'API Key', 'password'),
            inputField(local, 'ai_model', '模型名'),
            inputField(local, 'default_content_count', '默认内容数', 'number'),
            inputField(local, 'default_comment_count', '默认评论数', 'number'),
            selectField(local, 'content_cutoff_days', '内容截至日期', commentCutoffOptions),
            selectField(local, 'comment_cutoff_days', '评论截至日期', commentCutoffOptions),
            inputField(local, 'account_analysis_content_count', '账号分析内容数', 'number'),
            inputField(local, 'ai_analysis_concurrency', 'AI分析并行数', 'number', '建议 1-5，过高容易触发模型限流'),
            inputField(local, 'unreplied_reminder_days', '未回复提醒天数', 'number', '默认 3 天，填 0 表示不提醒'),
            inputField(local, 'douyin_detail_sleep_seconds', '抖音详情等待秒数', 'number', '建议 0.5-2，越小越快但越容易限流'),
            inputField(local, 'max_concurrency', '默认并发', 'number')
          ]),
          h('div', { class: 'toggles' }, [
            toggleField(local, 'headless', '默认无头模式'),
            toggleField(local, 'auto_analyze_competitors', '自动分析竞品账号'),
            toggleField(local, 'auto_delete_non_competitors', '自动删除非竞品账号'),
            toggleField(local, 'auto_analyze_leads', '自动分析线索用户'),
            toggleField(local, 'auto_delete_non_customers', '自动删除非客户账号')
          ]),
          h('div', { class: 'section-title compact' }, [h('h2', 'ICP画像'), h('span', 'AI筛选时会带入这些信息')]),
          h('div', { class: 'icp-grid' }, icpFields.map(field => renderIcpField(icpProfile, field))),
          h('div', { class: 'action-row' }, [
            h('button', {
              class: 'primary-action',
              onClick: () => emit('save', { ...local, icp_profile: buildIcpPayload(icpProfile) })
            }, [h(Check, { class: 'inline-icon' }), '保存设置'])
          ])
        ])
        ],
        side: () => [
        h('aside', { class: 'pane side-pane' }, [
          h('div', { class: 'section-title' }, [h('h2', '环境状态'), h('span', '运行前先检查')]),
          renderEnv(props.env),
          h('button', { class: 'wide-action', onClick: () => emit('check-env') }, [h(Refresh, { class: 'inline-icon' }), '重新检查']),
          renderTombstones(props.tombstoneSummary as Dict, props.tombstones as Dict, props.tombstoneFilters as Dict, filters => emit('load-tombstones', filters)),
          h('div', { class: 'danger-zone' }, [
            h('div', { class: 'section-title compact' }, [h('h2', '危险操作'), h('span', '不可恢复')]),
            h('p', '清空项目库和 MediaCrawler 底层库中的所有采集、线索、AI、日志数据。'),
            h('button', { class: 'wide-action danger-action', onClick: () => emit('clear-data') }, [h(Delete, { class: 'inline-icon' }), '清空所有数据'])
          ])
        ])
        ]
      })
    }
  }
})

function renderIcpField(profile: Dict, field: Dict) {
  const control = field.multiline
    ? h('textarea', {
        value: profile[field.key] || '',
        placeholder: field.placeholder,
        onInput: (event: Event) => profile[field.key] = (event.target as HTMLTextAreaElement).value
      })
    : h('input', {
        value: profile[field.key] || '',
        placeholder: field.placeholder,
        onInput: (event: Event) => profile[field.key] = (event.target as HTMLInputElement).value
      })
  return h('label', { class: ['icp-field', field.multiline ? 'icp-field-wide' : ''] }, [
    h('span', field.label),
    control
  ])
}

function inputField(local: Dict, key: string, label: string, type = 'text', placeholder = '') {
  const inputProps: Dict = {
    type,
    value: local[key] || '',
    placeholder,
    onInput: (event: Event) => local[key] = (event.target as HTMLInputElement).value
  }
  if (type === 'number') {
    inputProps.step = key === 'douyin_detail_sleep_seconds' ? '0.1' : '1'
    inputProps.min = key === 'douyin_detail_sleep_seconds' ? '0' : undefined
  }
  return h('label', [label, h('input', inputProps)])
}

function selectField(local: Dict, key: string, label: string, options: { value: number, label: string }[]) {
  const current = Number(local[key] ?? 0)
  return h('label', [
    label,
    h('select', {
      value: String(current),
      onChange: (event: Event) => local[key] = Number((event.target as HTMLSelectElement).value)
    }, options.map(option => h('option', { value: String(option.value) }, option.label)))
  ])
}

function toggleField(local: Dict, key: string, label: string) {
  return h('label', [h('input', { type: 'checkbox', checked: Boolean(local[key]), onChange: (event: Event) => local[key] = (event.target as HTMLInputElement).checked }), label])
}

function renderEnv(envValue: Dict) {
  const items = [
    ['项目库', envValue?.project_db],
    ['MediaCrawler路径', envValue?.media_crawler_path],
    ['底层SQLite', envValue?.media_crawler_db],
    ['AI配置', envValue?.ai_config]
  ]
  return h('div', { class: 'env-stack' }, [
    h('div', { class: 'env-list' }, items.map(([label, item]: any) => h('div', { class: 'env-item' }, [
      h('span', label),
      h('strong', { class: item?.ok ? 'ok' : 'warn' }, item?.ok ? '正常' : '待处理'),
      h('small', item?.path || item?.base_url || item?.model || '')
    ]))),
    renderProjectQuality(envValue?.project_quality || {}),
    renderPlatformDiagnostics(envValue?.platform_diagnostics || [])
  ])
}

function renderTombstones(summary: Dict, tombstones: Dict, filters: Dict, load: (filters: Dict) => void) {
  const localFilters = filters || {}
  const items = tombstones.items || []
  const page = Number(tombstones.page || 1)
  const totalPages = Number(tombstones.total_pages || 1)
  return h('div', { class: 'tombstone-panel' }, [
    h('div', { class: 'section-title compact' }, [
      h('h2', '防重复墓碑'),
      h('span', `共 ${summary.total || 0} 条`)
    ]),
    h('div', { class: 'quality-summary tombstone-summary' }, [
      renderQualityMetric('账号', summary.accounts || 0),
      renderQualityMetric('内容', summary.contents || 0),
      renderQualityMetric('评论', summary.comments || 0)
    ]),
    h('div', { class: 'tombstone-filters' }, [
      h('select', {
        value: localFilters.entity_type || '',
        onChange: (event: Event) => load({ entity_type: (event.target as HTMLSelectElement).value, page: 1 })
      }, [
        h('option', { value: '' }, '全部类型'),
        h('option', { value: 'author_account' }, '账号'),
        h('option', { value: 'content' }, '内容'),
        h('option', { value: 'comment' }, '评论')
      ]),
      h('input', {
        value: localFilters.query || '',
        placeholder: '搜索标识、来源或快照',
        onInput: (event: Event) => load({ query: (event.target as HTMLInputElement).value, page: 1 })
      })
    ]),
    items.length ? h('div', { class: 'tombstone-list' }, items.map((item: Dict) => h('article', [
      h('div', [
        h('strong', `${entityTypeLabel(item.entity_type)} · ${item.platform || '-'}`),
        h('small', `${item.identifier_type || '-'}: ${item.identifier_value || '-'}`)
      ]),
      h('p', item.snapshot_summary || '无快照摘要'),
      h('small', `${item.source || '未标记来源'} · ${item.updated_at || item.created_at || ''}`)
    ]))) : h('div', { class: 'diagnostic-empty' }, '当前没有墓碑记录'),
    h('div', { class: 'table-page-controls tombstone-pages' }, [
      h('button', { disabled: page <= 1, onClick: () => load({ page: Math.max(1, page - 1) }) }, '上一页'),
      h('span', `${page} / ${totalPages}`),
      h('button', { disabled: page >= totalPages, onClick: () => load({ page: Math.min(totalPages, page + 1) }) }, '下一页')
    ])
  ])
}

function renderProjectQuality(quality: Dict) {
  const summary = quality.summary || {}
  const sections = quality.sections || []
  const issues = quality.issues || []
  return h('div', { class: 'project-quality' }, [
    h('div', { class: 'section-title compact' }, [
      h('h2', '项目数据质量'),
      h('span', summary.status === 'ok' ? '关键字段完整' : `${summary.issues || 0} 项需要关注`)
    ]),
    h('div', { class: 'quality-summary' }, [
      renderQualityMetric('账号', summary.accounts || 0),
      renderQualityMetric('内容', summary.contents || 0),
      renderQualityMetric('评论', summary.comments || 0),
      renderQualityMetric('线索', summary.leads || 0)
    ]),
    h('div', { class: 'quality-sections' }, sections.map((section: Dict) => h('div', { class: 'quality-section' }, [
      h('div', { class: 'quality-section-head' }, [
        h('strong', section.label),
        h('span', `${section.total || 0} 条`)
      ]),
      h('div', { class: 'quality-fields' }, (section.fields || []).map((field: Dict) => renderQualityField(field)))
    ]))),
    issues.length
      ? h('ul', { class: 'quality-issues' }, issues.map((issue: Dict) => h('li', { class: `issue-${issue.severity || 'warning'}` }, [
        h('strong', issue.title),
        h('span', issue.detail)
      ])))
      : h('div', { class: 'diagnostic-empty' }, '项目库关键字段当前没有明显缺口')
  ])
}

function renderQualityMetric(label: string, value: number) {
  return h('div', [
    h('span', label),
    h('strong', String(value))
  ])
}

function renderQualityField(field: Dict) {
  const total = Number(field.total || 0)
  const nonEmpty = Number(field.non_empty || 0)
  const missing = total > 0 && nonEmpty < total
  return h('span', { class: missing ? 'field-warn' : '' }, `${field.label} ${nonEmpty}/${total}`)
}

function entityTypeLabel(type: string) {
  return ({ author_account: '账号', content: '内容', comment: '评论' } as Record<string, string>)[type] || type || '-'
}

function renderPlatformDiagnostics(platforms: Dict[]) {
  if (!platforms.length) {
    return h('div', { class: 'diagnostic-empty' }, '底层 SQLite 不存在或尚未完成检查')
  }
  return h('div', { class: 'platform-diagnostics' }, [
    h('div', { class: 'section-title compact' }, [h('h2', '平台数据诊断'), h('span', '原始表与关键字段')]),
    ...platforms.map(platform => h('div', { class: 'diagnostic-panel' }, [
      h('div', { class: 'diagnostic-head' }, [
        h('strong', platform.label || platform.platform),
        h('span', { class: platform.ok ? 'ok' : 'warn' }, platform.ok ? '可导入' : '缺内容表')
      ]),
      h('div', { class: 'diagnostic-tables' }, [
        renderRawTableMetric('内容', platform.tables?.content),
        renderRawTableMetric('评论', platform.tables?.comment),
        renderRawTableMetric('主页', platform.tables?.creator)
      ]),
      h('div', { class: 'field-quality' }, importantDiagnosticFields(platform).map(field => h('span', {
        class: field.supported && field.row_count && field.non_empty === 0 ? 'field-warn' : ''
      }, `${field.label} ${field.supported ? `${field.non_empty}/${field.row_count}` : '不支持'}`))),
      platform.warnings?.length ? h('ul', { class: 'diagnostic-warnings' }, platform.warnings.map((warning: string) => h('li', warning))) : null
    ]))
  ])
}

function renderRawTableMetric(label: string, table: Dict = {}) {
  return h('div', [
    h('span', label),
    h('strong', table.exists ? `${table.row_count || 0}` : '缺失'),
    h('small', table.table || '无映射')
  ])
}

function importantDiagnosticFields(platform: Dict) {
  const contentFields = platform.tables?.content?.fields || []
  const commentFields = platform.tables?.comment?.fields || []
  const creatorFields = platform.tables?.creator?.fields || []
  const pick = (fields: Dict[], key: string) => fields.find(field => field.key === key)
  return [
    pick(contentFields, 'author_id'),
    pick(contentFields, 'nickname'),
    pick(contentFields, 'signature'),
    pick(commentFields, 'body'),
    pick(creatorFields, 'signature'),
    pick(creatorFields, 'fans')
  ].filter((field): field is Dict => Boolean(field))
}
