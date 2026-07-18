import { defineComponent, h, reactive, ref, watch, type Component, type VNodeChild } from 'vue'
import { Check, DataAnalysis, Monitor, Refresh, Setting, Tools, User } from '@element-plus/icons-vue'
import type { Dict } from '../shared/types'
import { SplitPane } from '../components/ui/SplitPane'
import { TagInput, splitTagText } from '../components/ui/TagInput'
import { platformName } from '../shared/format'
import { pageAction, sectionTitle, type WorkbenchTone } from '../components/ui/Workbench'

const icpFields = [
  { key: 'product', label: '产品/服务', placeholder: '例如：AI客服、获客工具' },
  { key: 'company_name', label: '公司名（可选）', placeholder: '留空时，AI话术不得出现公司名或公司占位符' },
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
const ownAccountPlatforms = ['dy', 'xhs', 'ks']

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

function normalizeOwnAccounts(value: any) {
  let source = value
  if (typeof value === 'string') {
    try { source = JSON.parse(value) } catch { source = {} }
  }
  const result: Dict = { dy: [], xhs: [], ks: [] }
  ownAccountPlatforms.forEach(platform => {
    const raw = source?.[platform]
    result[platform] = Array.isArray(raw)
      ? raw.map(item => String(item).trim()).filter(Boolean)
      : splitTagText(String(raw || ''))
  })
  return result
}

function buildOwnAccountsPayload(value: Dict) {
  const result: Dict = { dy: [], xhs: [], ks: [] }
  ownAccountPlatforms.forEach(platform => {
    const tags = Array.isArray(value?.[platform]) ? value[platform] : splitTagText(String(value?.[platform] || ''))
    result[platform] = Array.from(new Set(tags.map((item: unknown) => String(item).trim()).filter(Boolean)))
  })
  return result
}

export default defineComponent({
  props: {
    settings: { type: Object, required: true },
    settingsSaveRevision: { type: Number, default: 0 },
    env: { type: Object, required: true }
  },
  emits: ['save', 'check-env', 'settings-dirty-change'],
  setup(props, { emit }) {
    const local = reactive<Dict>({})
    const settingsDirty = ref(false)
    const syncingFromProps = ref(false)

    function sync() {
      if (settingsDirty.value) return
      syncingFromProps.value = true
      Object.keys(local).forEach(key => delete local[key])
      Object.assign(local, JSON.parse(JSON.stringify(props.settings || {})))
      local.icp_profile = normalizeIcpProfile(local.icp_profile)
      local.own_accounts = normalizeOwnAccounts(local.own_accounts)
      emit('settings-dirty-change', false)
      syncingFromProps.value = false
    }
    function markSettingsDirty() {
      if (syncingFromProps.value) return
      settingsDirty.value = true
      emit('settings-dirty-change', true)
    }
    function submitSettings(payload: Dict) {
      emit('save', payload)
    }
    function submitSettingsAfterDraft(payloadFactory: () => Dict) {
      const active = document.activeElement as HTMLElement | null
      active?.blur()
      // 标签输入的 blur 会先把草稿提交为标签，再读取保存 payload。
      window.setTimeout(() => submitSettings(payloadFactory()), 0)
    }
    watch(() => props.settings, sync, { immediate: true, deep: true })
    watch(() => props.settingsSaveRevision, revision => {
      if (!revision) return
      settingsDirty.value = false
      emit('settings-dirty-change', false)
      sync()
    })
    return () => {
      if (!local.icp_profile || typeof local.icp_profile !== 'object') {
        local.icp_profile = normalizeIcpProfile(local.icp_profile)
      }
      if (!local.own_accounts || typeof local.own_accounts !== 'object') {
        local.own_accounts = normalizeOwnAccounts(local.own_accounts)
      }
      const icpProfile = local.icp_profile as Dict
      const ownAccounts = local.own_accounts as Dict
      return [
      h(SplitPane, { storageKey: 'settings', side: 'right', defaultSideWidth: 360 }, {
        default: () => [
        h('section', { class: 'pane primary-pane' }, [
          pageAction({
            title: '先补齐采集、AI 和客户画像配置',
            description: '路径、模型和 ICP 会影响采集导入、AI 判断和私信话术。',
            icon: Setting,
            tone: settingsDirty.value ? 'amber' : 'teal'
          }),
          settingsFoldPanel({
            title: '基础配置',
            subtitle: settingsDirty.value ? '有未保存修改，自动同步不会覆盖草稿' : '没有配置时 AI 分析会明确失败',
            icon: Tools,
            tone: settingsDirty.value ? 'amber' : 'teal'
          }, [
            h('div', { class: 'form-grid' }, [
              inputField(local, 'ai_base_url', 'AI服务地址', 'text', '', markSettingsDirty),
              inputField(local, 'ai_api_key', 'API Key', 'password', local.ai_api_key_configured ? '已配置，留空表示不修改' : '请输入 API Key', markSettingsDirty),
              inputField(local, 'ai_model', '模型名', 'text', '', markSettingsDirty),
              inputField(local, 'default_content_count', '默认内容数', 'number', '', markSettingsDirty),
              inputField(local, 'default_comment_count', '默认评论数', 'number', '', markSettingsDirty),
              selectField(local, 'content_cutoff_days', '内容截至日期', commentCutoffOptions, markSettingsDirty),
              selectField(local, 'comment_cutoff_days', '评论截至日期', commentCutoffOptions, markSettingsDirty),
              inputField(local, 'comment_recrawl_cooldown_hours', '评论复采间隔小时', 'number', '默认 24；填 0 表示每次找客户都复采已有内容评论', markSettingsDirty),
              inputField(local, 'account_analysis_content_count', '账号分析内容数', 'number', '', markSettingsDirty),
              inputField(local, 'ai_analysis_concurrency', 'AI分析并行数', 'number', '建议 1-5，过高容易触发模型限流', markSettingsDirty),
              inputField(local, 'douyin_detail_sleep_seconds', '抖音详情等待秒数', 'number', '建议 0.5-2，越小越快但越容易限流', markSettingsDirty),
              inputField(local, 'max_concurrency', '默认并发', 'number', '', markSettingsDirty)
            ]),
            h('div', { class: 'toggles' }, [
              toggleField(local, 'headless', '默认无头模式', markSettingsDirty),
              toggleField(local, 'auto_analyze_competitors', '自动分析竞品账号', markSettingsDirty),
              toggleField(local, 'auto_delete_non_competitors', '自动删除非竞品账号', markSettingsDirty),
              toggleField(local, 'auto_analyze_leads', '自动分析线索用户', markSettingsDirty),
              toggleField(local, 'auto_delete_non_customers', '自动删除非客户账号', markSettingsDirty)
            ])
          ]),
          settingsFoldPanel({ title: '自家账号', subtitle: '同平台可多个，跨平台分任务运行', icon: User, tone: 'blue' }, [
            h('div', { class: 'own-account-grid' }, ownAccountPlatforms.map(platform => renderOwnAccountField(ownAccounts, platform, markSettingsDirty)))
          ]),
          settingsFoldPanel({ title: 'ICP画像', subtitle: 'AI筛选时会带入这些信息', icon: DataAnalysis, tone: 'purple' }, [
            h('div', { class: 'icp-grid' }, icpFields.map(field => renderIcpField(icpProfile, field, markSettingsDirty)))
          ]),
          h('div', { class: 'action-row' }, [
            h('button', {
              class: 'primary-action',
              onClick: () => submitSettingsAfterDraft(() => ({ ...local, icp_profile: buildIcpPayload(icpProfile), own_accounts: buildOwnAccountsPayload(ownAccounts) }))
            }, [h(Check, { class: 'inline-icon' }), '保存设置'])
          ])
        ])
        ],
        side: () => [
        h('aside', { class: 'pane side-pane' }, [
          sectionTitle({ title: '环境状态', subtitle: '运行前先检查', icon: Monitor, tone: 'green' }),
          renderEnv(props.env),
          h('button', { class: 'wide-action', onClick: () => emit('check-env') }, [h(Refresh, { class: 'inline-icon' }), '重新检查'])
        ])
        ]
      })
      ]
    }
  }
})

function settingsFoldPanel(options: { title: string, subtitle: string, icon: Component, tone: WorkbenchTone }, children: VNodeChild[]) {
  return h('details', { class: 'settings-fold' }, [
    h('summary', [
      sectionTitle({ ...options, compact: true }),
      h('span', { class: 'settings-fold-hint' })
    ]),
    h('div', { class: 'settings-fold-body' }, children)
  ])
}

function renderOwnAccountField(accounts: Dict, platform: string, markDirty: () => void) {
  return h('label', { class: 'own-account-field' }, [
    h('span', `${platformName(platform)}自家账号主页/ID`),
    h(TagInput, {
      modelValue: accounts[platform] || [],
      placeholder: `输入${platformName(platform)}账号主页或ID后按回车`,
      onFocus: markDirty,
      'onUpdate:modelValue': (value: string[]) => {
        accounts[platform] = value
        markDirty()
      }
    }),
    h('small', '同平台可填多个；跨平台需要按平台分别运行任务。')
  ])
}

function renderIcpField(profile: Dict, field: Dict, markDirty: () => void) {
  const control = field.multiline
    ? h('textarea', {
        value: profile[field.key] || '',
        placeholder: field.placeholder,
        onFocus: markDirty,
        onCompositionstart: markDirty,
        onInput: (event: Event) => {
          profile[field.key] = (event.target as HTMLTextAreaElement).value
          markDirty()
        }
      })
    : h('input', {
        value: profile[field.key] || '',
        placeholder: field.placeholder,
        onFocus: markDirty,
        onCompositionstart: markDirty,
        onInput: (event: Event) => {
          profile[field.key] = (event.target as HTMLInputElement).value
          markDirty()
        }
      })
  return h('label', { class: ['icp-field', field.multiline ? 'icp-field-wide' : ''] }, [
    h('span', field.label),
    control
  ])
}

function inputField(local: Dict, key: string, label: string, type = 'text', placeholder = '', markDirty?: () => void) {
  const inputProps: Dict = {
    type,
    value: local[key] || '',
    placeholder,
    onFocus: markDirty,
    onCompositionstart: markDirty,
    onInput: (event: Event) => {
      local[key] = (event.target as HTMLInputElement).value
      markDirty?.()
    }
  }
  if (type === 'number') {
    inputProps.step = key === 'douyin_detail_sleep_seconds' ? '0.1' : '1'
    inputProps.min = key === 'douyin_detail_sleep_seconds' ? '0' : undefined
  }
  return h('label', [label, h('input', inputProps)])
}

function selectField(local: Dict, key: string, label: string, options: { value: number, label: string }[], markDirty?: () => void) {
  const current = Number(local[key] ?? 0)
  return h('label', [
    label,
    h('select', {
      value: String(current),
      onFocus: markDirty,
      onChange: (event: Event) => {
        local[key] = Number((event.target as HTMLSelectElement).value)
        markDirty?.()
      }
    }, options.map(option => h('option', { value: String(option.value) }, option.label)))
  ])
}

function toggleField(local: Dict, key: string, label: string, markDirty?: () => void) {
  return h('label', [h('input', {
    type: 'checkbox',
    checked: Boolean(local[key]),
    onFocus: markDirty,
    onChange: (event: Event) => {
      local[key] = (event.target as HTMLInputElement).checked
      markDirty?.()
    }
  }), label])
}

function renderEnv(envValue: Dict) {
  const items = [
    ['项目库', envValue?.project_db],
    ['数据库版本', envValue?.database_schema],
    ['采集组件', envValue?.collector_component],
    ['采集存储', envValue?.collector_storage],
    ['AI配置', envValue?.ai_config]
  ]
  return h('div', { class: 'env-stack' }, [
    h('div', { class: 'env-list' }, items.map(([label, item]: any) => h('div', { class: 'env-item' }, [
      h('span', label),
      h('strong', { class: item?.ok ? 'ok' : 'warn' }, item?.ok ? '正常' : '待处理'),
      h('small', item?.model || (item?.current !== undefined ? `${item.current}/${item.latest}` : ''))
    ])))
    // 环境检查详情暂时隐藏，只保留用户需要处理的概览状态。
  ])
}
