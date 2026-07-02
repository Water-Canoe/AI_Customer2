import { defineComponent, h, reactive } from 'vue'
import { ElDropdown, ElDropdownItem, ElDropdownMenu, ElMessage } from 'element-plus'
import type { Dict } from '../shared/types'
import { accountRoleLabel, competitorStatusClass, competitorStatusLabel, platformName, taskModeName } from '../shared/format'

const OVERVIEW_CHILD_PAGE_SIZE = 20

type OverviewHandlers = {
  analyzeAccount: (node: Dict) => void
  analyzeKeyword: (node: Dict) => void
  analyzeCustomerIntent: (node: Dict) => void
  analyzeAccountCustomers: (node: Dict) => void
  messageCustomer: (node: Dict) => void
  updateCustomerFollowStatus: (node: Dict, status: string) => void
  deleteAccount: (node: Dict) => void
  deleteCustomer: (node: Dict) => void
  deleteAccountNonCustomers: (node: Dict) => void
  deletePlatform: (node: Dict) => void
  deleteKeyword: (node: Dict) => void
  deleteKeywordNonCompetitors: (node: Dict) => void
  findCustomers: (node: Dict) => void
}

export default defineComponent({
  props: { tree: { type: Array, required: true } },
  emits: ['account-analyze', 'keyword-analyze', 'customer-intent-analyze', 'account-customers-analyze', 'customer-message', 'customer-follow-update', 'delete-account', 'delete-customer', 'delete-account-noncustomers', 'delete-platform', 'delete-keyword', 'delete-keyword-noncompetitors', 'find-customers'],
  setup(props, { emit }) {
    const expanded = reactive<Record<string, boolean>>({})
    const pages = reactive<Record<string, number>>({})
    const isExpanded = (node: Dict) => expanded[node.id] ?? false
    const toggle = (node: Dict) => {
      expanded[node.id] = !isExpanded(node)
    }
    const getPage = (node: Dict) => pages[node.id] || 1
    const setPage = (node: Dict, page: number) => {
      pages[node.id] = Math.max(1, page)
    }
    const handlers = {
      analyzeAccount: (node: Dict) => emit('account-analyze', node),
      analyzeKeyword: (node: Dict) => emit('keyword-analyze', node),
      analyzeCustomerIntent: (node: Dict) => emit('customer-intent-analyze', node),
      analyzeAccountCustomers: (node: Dict) => emit('account-customers-analyze', node),
      messageCustomer: (node: Dict) => emit('customer-message', node),
      updateCustomerFollowStatus: (node: Dict, status: string) => emit('customer-follow-update', node, status),
      deleteAccount: (node: Dict) => emit('delete-account', node),
      deleteCustomer: (node: Dict) => emit('delete-customer', node),
      deleteAccountNonCustomers: (node: Dict) => emit('delete-account-noncustomers', node),
      deletePlatform: (node: Dict) => emit('delete-platform', node),
      deleteKeyword: (node: Dict) => emit('delete-keyword', node),
      deleteKeywordNonCompetitors: (node: Dict) => emit('delete-keyword-noncompetitors', node),
      findCustomers: (node: Dict) => emit('find-customers', node)
    }
    return () => h('section', { class: 'pane overview-pane' }, [
      h('div', { class: 'section-title' }, [h('h2', '关系总览'), h('span', '平台 / 关键词 / 账号层级表')]),
      (props.tree as Dict[]).length
        ? h('div', { class: 'overview-table' }, (props.tree as Dict[]).flatMap(node => renderOverviewRow(node, 0, isExpanded, toggle, getPage, setPage, handlers)))
        : h('div', { class: 'empty-state' }, '暂无总览数据，请先创建采集任务')
    ])
  }
})

function renderOverviewRow(
  node: Dict,
  level: number,
  isExpanded: (node: Dict) => boolean,
  toggle: (node: Dict) => void,
  getPage: (node: Dict) => number,
  setPage: (node: Dict, page: number) => void,
  handlers: OverviewHandlers
): any[] {
  const children = node.children || []
  const opened = isExpanded(node)
  const row = node.kind === 'account'
    ? renderOverviewAccountRow(node, level, handlers, children.length ? opened : false, children.length ? () => toggle(node) : undefined)
    : node.kind === 'customer'
      ? renderOverviewCustomerRow(node, level, handlers)
    : h('div', { class: ['overview-row', `level-${level}`, node.kind] }, [
        h('div', { class: 'overview-main', style: { paddingLeft: `${level * 34}px` } }, [
          children.length
            ? h('button', { class: 'outline-button', onClick: () => toggle(node) }, opened ? '收起' : '展开')
            : h('span', { class: 'overview-spacer' }),
        h('div', { class: 'overview-title' }, [
            renderOverviewTitle(node),
            h('small', overviewSubtitle(node))
          ])
        ]),
        h('div', { class: 'overview-metrics' }, [
          ...overviewMetricChips(node).map(chip => h('span', { class: 'metric-chip' }, chip)),
          ...overviewActions(node, handlers)
        ])
      ])
  const rows = [
    row
  ]
  if (opened) {
    // Each expanded node owns pagination so large account/customer lists stay navigable.
    const pagination = paginateChildren(node, children, getPage)
    pagination.items.forEach((child: Dict) => rows.push(...renderOverviewRow(child, level + 1, isExpanded, toggle, getPage, setPage, handlers)))
    if (pagination.totalPages > 1) {
      rows.push(renderOverviewPagination(node, level + 1, pagination, setPage))
    }
  }
  return rows
}

function paginateChildren(node: Dict, children: Dict[], getPage: (node: Dict) => number) {
  const total = children.length
  const totalPages = Math.max(1, Math.ceil(total / OVERVIEW_CHILD_PAGE_SIZE))
  const page = Math.min(Math.max(getPage(node), 1), totalPages)
  const start = (page - 1) * OVERVIEW_CHILD_PAGE_SIZE
  const end = Math.min(start + OVERVIEW_CHILD_PAGE_SIZE, total)
  return {
    items: children.slice(start, end),
    page,
    total,
    totalPages,
    start: total ? start + 1 : 0,
    end
  }
}

function renderOverviewPagination(
  node: Dict,
  level: number,
  pagination: Dict,
  setPage: (node: Dict, page: number) => void
) {
  return h('div', { class: 'overview-pagination-row', style: { paddingLeft: `${level * 34}px` } }, [
    h('span', `显示 ${pagination.start}-${pagination.end} / ${pagination.total}`),
    h('div', { class: 'overview-pagination-actions' }, [
      h('button', {
        disabled: pagination.page <= 1,
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          setPage(node, pagination.page - 1)
        }
      }, '上一页'),
      h('strong', `${pagination.page} / ${pagination.totalPages}`),
      h('button', {
        disabled: pagination.page >= pagination.totalPages,
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          setPage(node, pagination.page + 1)
        }
      }, '下一页')
    ])
  ])
}

function renderOverviewAccountRow(
  node: Dict,
  level: number,
  handlers: OverviewHandlers,
  opened: boolean,
  toggle?: () => void
) {
  const metrics = node.metrics || {}
  const signature = metrics.signature || '主页简介未采集，需账号分析'
  const reason = metrics.competitor_reason || '暂无分析原因'
  const status = overviewAccountStatus(metrics)
  return h('div', { class: ['overview-row', 'overview-account-row', `level-${level}`, node.kind] }, [
    h('div', { class: 'account-cell account-identity', style: { paddingLeft: `${level * 34}px` } }, [
      h('div', { class: 'account-identity-head' }, [
        toggle
          ? h('button', { class: 'outline-button account-expand', onClick: (event: MouseEvent) => { event.stopPropagation(); toggle() } }, opened ? '收起' : '展开')
          : h('span', { class: 'overview-spacer compact' }),
        h('div', { class: 'account-title-wrap' }, [
          h('div', { class: 'account-title-line' }, [
            renderOverviewTitle(node),
            metrics.fans !== null && metrics.fans !== undefined ? h('span', { class: 'account-fans' }, `粉丝 ${metrics.fans}`) : null
          ]),
          h('small', { title: signature }, signature)
        ])
      ]),
    ]),
    h('div', { class: 'account-cell account-reason', title: reason }, [
      h('small', 'AI分析原因'),
      h('span', reason)
    ]),
    h('div', { class: 'account-cell account-status' }, [
      h('span', { class: ['account-status-pill', competitorStatusClass(status)] }, status)
    ]),
    h('div', { class: 'account-cell account-stats' }, [
      h('div', { class: 'account-counts' }, [
        h('span', `内容总数 ${formatCount(metrics.content_total_count, '-')}`),
        h('span', `已爬取内容 ${metrics.crawled_content_count ?? metrics.content_count ?? 0}`),
        h('span', `评论 ${metrics.comment_count || 0}`),
        h('span', `线索 ${metrics.customer_count || 0}`),
        h('span', `客户数 ${metrics.target_customer_count || 0}`),
        h('span', `非客户数 ${metrics.non_customer_count || 0}`)
      ]),
      h('small', metrics.latest ? `最近 ${metrics.latest}` : '暂无时间')
    ]),
    h('div', { class: 'account-cell account-actions' }, overviewActions(node, handlers))
  ])
}

function renderOverviewCustomerRow(node: Dict, level: number, handlers: OverviewHandlers) {
  const metrics = node.metrics || {}
  const reason = metrics.reason || '暂无AI分析原因'
  const rawScript = String(metrics.script || '').trim()
  const script = rawScript || '暂无AI生成话术'
  const videoText = overviewSampleText(metrics.content_samples, '暂无视频详情')
  const commentText = overviewSampleText(metrics.comment_samples, metrics.signature || '暂无评论摘要')
  const firstContentUrl = firstOverviewSample(metrics.content_urls)
  const aiStatus = customerAiAnalysisStatus(metrics)
  const followStatus = String(metrics.follow_status || metrics.screening_status || '待筛选')
  const screeningStage = customerScreeningStage(metrics)
  return h('div', { class: ['overview-row', 'overview-customer-row', `level-${level}`, node.kind] }, [
    h('div', { class: 'account-cell account-identity customer-identity', style: { paddingLeft: `${level * 34}px` } }, [
      h('div', { class: 'account-title-line' }, [
        renderOverviewTitle(node)
      ]),
      h('small', { title: commentText }, commentText)
    ]),
    h('div', { class: 'account-cell account-reason customer-video', title: videoText }, [
      h('small', '视频详情'),
      h('span', videoText),
      firstContentUrl ? h('a', { href: firstContentUrl, target: '_blank', rel: 'noreferrer' }, '打开视频') : null
    ]),
    h('div', {
      class: ['account-cell', 'account-reason', 'customer-script', rawScript ? 'copyable-script' : 'empty-script'],
      title: rawScript ? `${script}\n点击复制AI话术` : script,
      role: rawScript ? 'button' : undefined,
      tabindex: rawScript ? 0 : undefined,
      onClick: rawScript ? (event: MouseEvent) => copyAiScript(rawScript, event) : undefined,
      onKeydown: rawScript ? (event: KeyboardEvent) => {
        if (event.key === 'Enter' || event.key === ' ') copyAiScript(rawScript, event)
      } : undefined
    }, [
      h('small', rawScript ? 'AI生成话术 · 点击复制' : 'AI生成话术'),
      h('span', script)
    ]),
    h('div', { class: 'account-cell account-reason', title: reason }, [
      h('small', 'AI分析原因'),
      h('span', reason)
    ]),
    h('div', { class: 'account-cell account-reason customer-status-panel' }, [
      h('small', 'AI分析状态'),
      h('div', { class: 'customer-status-stack' }, [
        h('div', { class: 'customer-status-line customer-status-head' }, [
          h('span', { class: ['customer-ai-status', customerAiStatusClass(aiStatus)] }, aiStatus),
          renderCustomerScreeningStatusDropdown(node, screeningStage, handlers)
        ]),
        screeningStage === '客户'
          ? h('div', { class: 'customer-status-line customer-follow-line' }, [
              renderCustomerFollowStatusDropdown(node, followStatus, handlers)
            ])
          : null,
        h('div', { class: 'customer-status-line customer-status-meta' }, [
          metrics.intention ? h('b', `意向 ${metrics.intention}`) : null,
          h('b', `证据 ${metrics.source_count || 0}`)
        ])
      ]),
      h('small', metrics.latest ? `最近 ${metrics.latest}` : '暂无时间')
    ]),
    h('div', { class: 'account-cell account-actions customer-actions' }, [
      h('button', {
        class: 'overview-action',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          handlers.analyzeCustomerIntent(node)
        }
      }, '意向分析'),
      h('button', {
        class: 'overview-action reserved',
        disabled: screeningStage !== '客户',
        title: screeningStage === '客户' ? '复制AI话术、打开主页并标记已私信' : '只有客户状态才能私信',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          if (screeningStage === '客户') handlers.messageCustomer(node)
        }
      }, '私信'),
      h('button', {
        class: 'overview-action danger',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          handlers.deleteCustomer(node)
        }
      }, '删除')
    ])
  ])
}

async function copyAiScript(value: unknown, event?: MouseEvent | KeyboardEvent) {
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

function renderCustomerScreeningStatusDropdown(node: Dict, stage: string, handlers: OverviewHandlers) {
  const actions = customerScreeningActions(stage)
  return h(ElDropdown, {
    trigger: 'click',
    teleported: true,
    onCommand: (status: string) => handlers.updateCustomerFollowStatus(node, status)
  }, {
    default: () => h('button', {
      class: ['customer-screening-status', customerScreeningStatusClass(stage)],
      type: 'button',
      title: '点击修改客户状态'
    }, stage),
    dropdown: () => h(ElDropdownMenu, null, {
      default: () => actions.map(action => h(ElDropdownItem, { command: action.status }, {
        default: () => action.label
      }))
    })
  })
}

function customerScreeningActions(stage: string) {
  if (stage === '客户') {
    return [
      { status: '非客户', label: '标记非客户' },
      { status: '待筛选', label: '恢复未筛选' }
    ]
  }
  if (stage === '非客户') {
    return [
      { status: '待筛选', label: '恢复未筛选' },
      { status: '未私信', label: '标记客户' }
    ]
  }
  return [
    { status: '未私信', label: '标记客户' },
    { status: '非客户', label: '标记非客户' }
  ]
}

function renderCustomerFollowStatusDropdown(node: Dict, followStatus: string, handlers: OverviewHandlers) {
  const actions = customerFollowActions(followStatus)
  return h(ElDropdown, {
    trigger: 'click',
    teleported: true,
    onCommand: (status: string) => handlers.updateCustomerFollowStatus(node, status)
  }, {
    default: () => h('button', {
      class: ['customer-follow-status', 'customer-follow-button', customerFollowStatusClass(followStatus)],
      type: 'button',
      title: '点击修改跟进状态'
    }, followStatus),
    dropdown: () => h(ElDropdownMenu, null, {
      default: () => actions.map(action => h(ElDropdownItem, { command: action.status }, {
        default: () => action.label
      }))
    })
  })
}

function customerFollowActions(status: string) {
  const current = status || '待筛选'
  if (current === '未私信') {
    return [
      { status: '已私信', label: '标记已私信' },
    ]
  }
  if (['未回复', '已私信'].includes(current)) {
    return [
      { status: '未私信', label: '改回未私信' },
      { status: '已回复', label: '标记已回复' },
      { status: '未成交', label: '标记未成交' }
    ]
  }
  if (current === '已回复') {
    return [
      { status: '已成交', label: '标记已成交' },
      { status: '未成交', label: '标记未成交' },
      { status: '未回复', label: '改回未回复' },
      { status: '未私信', label: '改回未私信' }
    ]
  }
  if (current === '未成交') {
    return [
      { status: '已成交', label: '改为已成交' },
      { status: '已回复', label: '改回已回复' },
      { status: '未回复', label: '改回未回复' },
      { status: '未私信', label: '改回未私信' }
    ]
  }
  if (current === '已成交') {
    return [
      { status: '已回复', label: '改回已回复' },
      { status: '未成交', label: '改为未成交' },
      { status: '未回复', label: '改回未回复' },
      { status: '未私信', label: '改回未私信' }
    ]
  }
  return [
    { status: '未私信', label: '设为未私信' }
  ]
}

function renderOverviewTitle(node: Dict) {
  const label = displayOverviewLabel(node)
  if (['account', 'customer'].includes(node.kind) && node.metrics?.profile_url) {
    return h('a', { class: 'overview-link', href: node.metrics.profile_url, target: '_blank', rel: 'noreferrer' }, label)
  }
  return h('strong', label)
}

function displayOverviewLabel(node: Dict) {
  if (node.kind === 'platform') return platformName(node.label)
  if (node.kind === 'keyword') return `关键词：${node.label}`
  if (node.kind === 'source_group') return `账号任务：${node.metrics?.source_label || taskModeName(node.metrics?.source_mode || node.label)}`
  return node.label || `账号 ${node.metrics?.id || ''}`
}

function overviewSubtitle(node: Dict) {
  if (node.kind === 'platform') return node.label
  if (node.kind === 'keyword') return '关键词分组'
  if (node.kind === 'source_group') return '无关键词来源'
  if (node.kind === 'customer') return '客户账号'
  if (node.kind === 'account') {
    const signature = node.metrics?.signature || '主页简介未采集，需账号分析'
    return signature
  }
  return '账号'
}

function overviewActions(node: Dict, handlers: OverviewHandlers) {
  const metrics = node.metrics || {}
  if (node.kind === 'platform') {
    return [
      h('button', {
        class: 'overview-action danger',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          handlers.deletePlatform(node)
        }
      }, '删除')
    ]
  }
  if (node.kind === 'keyword') {
    const canAnalyzeKeyword = ['dy', 'xhs'].includes(metrics.platform)
    return [
      h('button', {
        class: 'overview-action reserved',
        disabled: !canAnalyzeKeyword,
        title: canAnalyzeKeyword ? '为该关键词下所有未分析账号创建账号分析任务' : '当前平台不支持主页资料采集',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          if (canAnalyzeKeyword) handlers.analyzeKeyword(node)
        }
      }, '一键竞品分析'),
      h('button', {
        class: 'overview-action reserved',
        title: '采集该关键词下竞品账号的内容评论区，生成线索客户',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          handlers.findCustomers(node)
        }
      }, '一键找客户'),
      h('button', {
        class: 'overview-action danger',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          handlers.deleteKeywordNonCompetitors(node)
        }
      }, '一键删除非竞品'),
      h('button', {
        class: 'overview-action danger',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          handlers.deleteKeyword(node)
        }
      }, '删除')
    ]
  }
  if (node.kind !== 'account') return []
  const isCompetitor = metrics.competitor_status === '竞品'
  const isOwnAccount = Boolean(metrics.is_own_account) || metrics.account_role === 'own_account'
  const canAnalyze = ['dy', 'xhs'].includes(metrics.platform)
  const isAnalysisBusy = ['排队分析', '正在分析'].includes(overviewAccountStatus(metrics))
  if (isOwnAccount) {
    return [
      h('button', {
        class: 'overview-action danger',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          handlers.deleteAccount(node)
        }
      }, '删除')
    ]
  }
  if (isCompetitor) {
    return [
      h('button', {
        class: 'overview-action reserved',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          handlers.findCustomers(node)
        }
      }, '找客户'),
      h('button', {
        class: 'overview-action',
        title: '对该竞品账号下所有客户账号执行AI意向分析',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          handlers.analyzeAccountCustomers(node)
        }
      }, '一键意向分析'),
      h('button', {
        class: 'overview-action danger',
        title: '删除已判定为非客户的客户账号',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          handlers.deleteAccountNonCustomers(node)
        }
      }, '删除非客户'),
      h('button', {
        class: 'overview-action danger',
        onClick: (event: MouseEvent) => {
          event.stopPropagation()
          handlers.deleteAccount(node)
        }
      }, '删除')
    ]
  }
  return [
    h('button', {
      class: 'overview-action',
      disabled: !canAnalyze || isAnalysisBusy,
      title: isAnalysisBusy ? '账号已在分析流程中' : canAnalyze ? '采集主页和少量视频后交给AI判断竞品' : '当前平台不支持主页资料采集',
      onClick: (event: MouseEvent) => {
        event.stopPropagation()
        if (canAnalyze && !isAnalysisBusy) handlers.analyzeAccount(node)
      }
    }, '账号分析'),
    h('button', {
      class: 'overview-action danger',
      onClick: (event: MouseEvent) => {
        event.stopPropagation()
        handlers.deleteAccount(node)
      }
    }, '删除')
  ]
}

function overviewMetricChips(node: Dict) {
  const metrics = node.metrics || {}
  if (node.kind === 'account') {
    return [
      accountRoleLabel(metrics.account_role) ? `角色 ${accountRoleLabel(metrics.account_role)}` : '',
      overviewAccountStatus(metrics),
      metrics.fans !== null && metrics.fans !== undefined ? `粉丝 ${metrics.fans}` : '',
      `内容总数 ${formatCount(metrics.content_total_count, '-')}`,
      `已爬取内容 ${metrics.crawled_content_count ?? metrics.content_count ?? 0}`,
      `评论 ${metrics.comment_count || 0}`,
      `线索 ${metrics.customer_count || 0}`,
      `客户数 ${metrics.target_customer_count || 0}`,
      `非客户数 ${metrics.non_customer_count || 0}`,
      metrics.latest ? `最近 ${metrics.latest}` : ''
    ].filter(Boolean)
  }
  return [
    `竞品 ${metrics.competitors || 0}`,
    `内容 ${metrics.contents || 0}`,
    `评论 ${metrics.comments || 0}`,
    `线索用户 ${metrics.customers || 0}`,
    metrics.latest ? `最近 ${metrics.latest}` : ''
  ].filter(Boolean)
}

function overviewAccountStatus(metrics: Dict) {
  // 真实结论仍用 competitor_status；display 字段只承载队列/运行进度。
  if (Boolean(metrics.is_own_account) || metrics.account_role === 'own_account') return '自家账号'
  return competitorStatusLabel(metrics.competitor_display_status || metrics.competitor_status)
}

function customerAiAnalysisStatus(metrics: Dict) {
  // 客户 AI 状态只做进度展示，不覆盖真实跟进状态。
  return String(metrics.ai_analysis_status || '未分析')
}

function customerAiStatusClass(status: string) {
  if (status === '正在分析') return 'is-running'
  if (status === '排队分析') return 'is-queued'
  if (status === '已分析') return 'is-done'
  return 'is-unknown'
}

function customerScreeningStage(metrics: Dict) {
  const screening = String(metrics.screening_status || '')
  const follow = String(metrics.follow_status || '')
  if (screening === '非客户' || ['非客户', '无需跟进'].includes(follow)) return '非客户'
  if (screening === '目标客户' || ['未私信', '已私信', '未回复', '已回复', '未成交', '已成交'].includes(follow)) return '客户'
  return '未筛选'
}

function customerScreeningStatusClass(stage: string) {
  if (stage === '客户') return 'is-customer'
  if (stage === '非客户') return 'is-not-customer'
  return 'is-unscreened'
}

function customerFollowStatusClass(status: string) {
  if (['已成交'].includes(status)) return 'is-won'
  if (['已回复'].includes(status)) return 'is-replied'
  if (['未回复', '已私信'].includes(status)) return 'is-waiting'
  if (['未私信', '目标客户'].includes(status)) return 'is-unmessaged'
  if (['未成交'].includes(status)) return 'is-lost'
  if (['非客户', '无需跟进'].includes(status)) return 'is-not-customer'
  if (['已移出', '隐藏'].includes(status)) return 'is-removed'
  if (['待筛选', '未分析'].includes(status)) return 'is-screening'
  return 'is-unknown'
}

function formatCount(value: unknown, fallback = '0') {
  if (value === null || value === undefined || value === '') return fallback
  const numberValue = Number(value)
  return Number.isFinite(numberValue) ? String(numberValue) : fallback
}

function overviewSampleText(value: unknown, fallback: string) {
  const items = String(value || '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
    .slice(0, 3)
  return items.length ? items.join('\n') : fallback
}

function firstOverviewSample(value: unknown) {
  return String(value || '').split(',').map(item => item.trim()).find(Boolean) || ''
}


