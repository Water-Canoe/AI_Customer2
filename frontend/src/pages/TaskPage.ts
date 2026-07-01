import { computed, defineComponent, h, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { VideoPlay } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { api } from '../shared/api'
import type { Dict } from '../shared/types'
import { platformName, taskModeName } from '../shared/format'
import { SplitPane } from '../components/ui/SplitPane'
import { TagInput, joinTags, splitTagText } from '../components/ui/TagInput'

function renderCapabilityPanel(capability: Dict, modeCapability: Dict) {
  // 能力提示只解释平台字段限制，不直接修改任务参数。
  if (!capability?.platform || !modeCapability?.mode) {
    return h('section', { class: 'capability-panel muted' }, [
      h('strong', '平台能力读取中'),
      h('small', '刷新后会显示当前平台和模式的数据字段限制')
    ])
  }
  const fields = capability.fields || {}
  const fieldItems = [
    fields.content_signature,
    fields.creator_signature,
    fields.comment_signature,
    fields.creator_fans
  ].filter(Boolean)
  const warnings = Array.from(new Set<string>([
    ...(modeCapability.warnings || []),
    ...(capability.warnings || [])
  ])).slice(0, 5)
  return h('section', { class: 'capability-panel' }, [
    h('div', { class: 'capability-head' }, [
      h('div', [
        h('small', `${platformName(capability.platform)} · ${modeCapability.label}`),
        h('strong', `${modeCapability.required_input} → ${(modeCapability.expected_outputs || []).join(' / ')}`)
      ]),
      h('span', { class: 'capability-type' }, `MediaCrawler: ${modeCapability.crawler_type}`)
    ]),
    h('div', { class: 'capability-fields' }, fieldItems.map((field: Dict) => h('span', {
      class: ['capability-chip', field.supported ? `status-${field.status}` : 'status-unsupported'],
      title: field.note
    }, [
      h('em', field.label),
      h('strong', field.status_label)
    ]))),
    warnings.length ? h('ul', { class: 'capability-warnings' }, warnings.map(warning => h('li', warning))) : null
  ])
}

function renderTaskPreviewPanel(preview: Dict | null, error: string, loading: boolean) {
  const normalized = preview?.normalized || {}
  const mainInput = normalized.keywords || normalized.creator_id || normalized.specified_id || '-'
  return h('section', { class: ['task-preview', error ? 'has-error' : ''] }, [
    h('div', { class: 'preview-head' }, [
      h('div', [
        h('small', '执行预览'),
        h('strong', loading ? '正在计算实际参数...' : (error || '确认后会按以下命令启动 MediaCrawler'))
      ]),
      preview ? h('span', { class: 'preview-status' }, `${platformName(preview.platform)} / ${preview.crawler_type}`) : null
    ]),
    preview ? h('div', { class: 'preview-grid' }, [
      previewItem('任务名', preview.name),
      previewItem('输入', mainInput),
      previewItem('内容数', normalized.content_count),
      previewItem('单条评论', normalized.comment_count),
      previewItem('采评论', normalized.collect_comments ? '是' : '否'),
      previewItem('立即运行', normalized.execute_crawler ? '是' : '否')
    ]) : null,
    preview?.command_text ? h('pre', { class: 'command-line' }, preview.command_text) : null,
    preview?.warnings?.length ? h('ul', { class: 'preview-warnings' }, preview.warnings.map((warning: string) => h('li', warning))) : null
  ])
}

function previewItem(label: string, value: unknown) {
  return h('span', { class: 'preview-field' }, [
    h('em', label),
    h('strong', String(value ?? '-'))
  ])
}

export default defineComponent({
  props: {
    tasks: { type: Array, required: true },
    settings: { type: Object, required: true },
    capabilities: { type: Array, required: true },
    retryDraft: { type: Object, default: null }
  },
  emits: ['create-task', 'open-logs', 'consume-retry-draft'],
  setup(props, { emit }) {
    const modes = [
      { key: 'competitor_discovery', title: '竞品账号采集', note: '关键词找竞品候选，再用AI确认', badge: '找账号' },
      { key: 'competitor_crawl', title: '竞品账号爬取', note: '爬评论区，把评论用户转为线索', badge: '找线索' },
      { key: 'demand_content', title: '找需求内容', note: '关键词找吐槽/需求内容，作者进入客户池', badge: '找需求' },
      { key: 'own_account', title: '自家账号互动', note: '监控自家评论区，筛出高意向用户', badge: '自有流量' }
    ]
    function settingNumber(key: string, fallback: number, minimum = 1) {
      const value = Number((props.settings as Dict)?.[key])
      return Number.isFinite(value) && value >= minimum ? value : fallback
    }
    function boolValue(value: unknown) {
      return value === true || value === 1 || value === '1'
    }
    const form = reactive({
      mode: 'competitor_discovery',
      platform: 'dy',
      login_type: 'qrcode',
      keyword_tags: [] as string[],
      creator_id_tags: [] as string[],
      specified_id_tags: [] as string[],
      content_count: settingNumber('default_content_count', 20),
      comment_count: settingNumber('default_comment_count', 20, 0),
      max_concurrency: settingNumber('max_concurrency', 1),
      collect_comments: false,
      collect_sub_comments: false,
      headless: boolValue((props.settings as Dict)?.headless),
      tcp_mode: true,
      execute_crawler: true
    })
    const prefillSource = ref<Dict | null>(null)
    const settingsDefaultsApplied = ref(false)
    const modeNeedsCreator = computed(() => ['competitor_crawl', 'own_account'].includes(form.mode))
    const modeUsesKeywords = computed(() => ['competitor_discovery', 'demand_content'].includes(form.mode))
    const activeCapability = computed(() => (props.capabilities as Dict[]).find(item => item.platform === form.platform) || {})
    const activeModeCapability = computed(() => activeCapability.value?.modes?.[form.mode] || {})
    const taskPreview = ref<Dict | null>(null)
    const previewError = ref('')
    const previewLoading = ref(false)
    let previewTimer: ReturnType<typeof setTimeout> | null = null
    function applyMode(modeKey: string) {
      prefillSource.value = null
      form.mode = modeKey
      form.collect_comments = ['competitor_crawl', 'own_account'].includes(modeKey)
      form.collect_sub_comments = form.collect_comments
      if (['competitor_discovery', 'demand_content'].includes(modeKey)) {
        form.creator_id_tags = []
        form.specified_id_tags = []
      } else {
        form.keyword_tags = []
      }
    }
    function applySettingsDefaultsOnce() {
      if (settingsDefaultsApplied.value || prefillSource.value) return
      if (!Object.keys(props.settings || {}).length) return
      // 设置页默认值只在首次加载任务页时回填，避免覆盖用户正在编辑的参数。
      form.content_count = settingNumber('default_content_count', 20)
      form.comment_count = settingNumber('default_comment_count', 20)
      form.max_concurrency = settingNumber('max_concurrency', 1)
      form.headless = boolValue((props.settings as Dict).headless)
      settingsDefaultsApplied.value = true
    }
    function applyTaskDraft(task: Dict) {
      // 失败任务重试只回填表单，真正创建仍走任务管理页的提交入口。
      form.mode = String(task.mode || 'competitor_discovery')
      form.platform = ['dy', 'xhs', 'ks'].includes(task.platform) ? task.platform : 'dy'
      form.login_type = ['qrcode', 'phone', 'cookie'].includes(task.login_type) ? task.login_type : 'qrcode'
      form.keyword_tags = splitTagText(String(task.keywords || ''))
      form.creator_id_tags = splitTagText(String(task.creator_id || ''))
      form.specified_id_tags = splitTagText(String(task.specified_id || ''))
      form.content_count = Number(task.content_count || 20)
      form.comment_count = Number(task.comment_count || 20)
      form.max_concurrency = Number(task.max_concurrency || 1)
      form.collect_comments = boolValue(task.collect_comments)
      form.collect_sub_comments = boolValue(task.collect_sub_comments)
      form.headless = boolValue(task.headless)
      form.tcp_mode = task.tcp_mode === undefined ? true : boolValue(task.tcp_mode)
      form.execute_crawler = task.execute_crawler === undefined ? true : boolValue(task.execute_crawler)
      prefillSource.value = task
    }
    function buildTaskPayload() {
      const payload = {
        mode: form.mode,
        platform: form.platform,
        login_type: form.login_type,
        keywords: joinTags(form.keyword_tags),
        creator_id: joinTags(form.creator_id_tags),
        specified_id: joinTags(form.specified_id_tags),
        content_count: form.content_count,
        comment_count: form.comment_count,
        max_concurrency: form.max_concurrency,
        collect_comments: form.collect_comments,
        collect_sub_comments: form.collect_sub_comments,
        headless: form.headless,
        tcp_mode: form.tcp_mode,
        execute_crawler: form.execute_crawler
      }
      if (['competitor_discovery', 'demand_content'].includes(payload.mode)) {
        payload.creator_id = ''
        payload.specified_id = ''
      } else if (['account_analysis', 'profile_enrichment'].includes(payload.mode)) {
        payload.keywords = ''
        payload.specified_id = ''
      } else if (payload.specified_id) {
        payload.keywords = ''
        payload.creator_id = ''
      } else {
        payload.keywords = ''
      }
      return payload
    }
    async function refreshPreview() {
      previewLoading.value = true
      const payload = buildTaskPayload()
      const localError = previewInputError(payload)
      if (localError) {
        taskPreview.value = null
        previewError.value = localError
        previewLoading.value = false
        return
      }
      try {
        const { data } = await api.post('/tasks/preview', payload)
        taskPreview.value = data
        previewError.value = ''
      } catch (error: any) {
        taskPreview.value = null
        previewError.value = error?.response?.data?.detail || '任务参数还不完整'
      } finally {
        previewLoading.value = false
      }
    }
    function previewInputError(payload: Dict) {
      if (['competitor_discovery', 'demand_content'].includes(payload.mode) && !payload.keywords) {
        return '搜索型任务必须填写关键词，避免使用 MediaCrawler 默认关键词'
      }
      if (['account_analysis', 'profile_enrichment'].includes(payload.mode) && !payload.creator_id) {
        return '账号资料任务必须填写创作者主页/ID'
      }
      if (!['competitor_discovery', 'demand_content'].includes(payload.mode) && !payload.creator_id && !payload.specified_id) {
        return '账号/详情采集任务必须填写创作者主页/ID或指定内容ID'
      }
      return ''
    }
    function schedulePreview() {
      if (previewTimer) clearTimeout(previewTimer)
      previewTimer = setTimeout(refreshPreview, 250)
    }
    watch(
      () => [
        form.mode,
        form.platform,
        form.login_type,
        form.keyword_tags.join('|'),
        form.creator_id_tags.join('|'),
        form.specified_id_tags.join('|'),
        form.content_count,
        form.comment_count,
        form.max_concurrency,
        form.collect_comments,
        form.collect_sub_comments,
        form.headless,
        form.tcp_mode,
        form.execute_crawler
      ],
      schedulePreview,
      { immediate: true }
    )
    watch(
      () => props.settings,
      applySettingsDefaultsOnce,
      { immediate: true, deep: true }
    )
    watch(
      () => props.retryDraft,
      draft => {
        if (!draft) return
        applyTaskDraft(draft as Dict)
        emit('consume-retry-draft')
      },
      { immediate: true }
    )
    onBeforeUnmount(() => {
      if (previewTimer) clearTimeout(previewTimer)
    })
    function submit() {
      const payload = buildTaskPayload()
      if (modeUsesKeywords.value && !payload.keywords) {
        ElMessage.error('搜索型任务必须填写关键词')
        return
      }
      if (!modeUsesKeywords.value && !payload.creator_id && !payload.specified_id) {
        ElMessage.error('账号/详情采集任务必须填写创作者主页/ID或指定内容ID')
        return
      }
      emit('create-task', payload)
      prefillSource.value = null
    }
    return () => h(SplitPane, { storageKey: 'tasks', side: 'right', defaultSideWidth: 360 }, {
      default: () => [
      h('section', { class: 'pane primary-pane' }, [
        h('div', { class: 'section-title' }, [h('h2', '选择拓客模式'), h('span', '先选目标，再填必要参数')]),
        prefillSource.value ? h('div', { class: 'retry-prefill' }, [
          h('strong', `重试 ${prefillSource.value.id || ''} · ${taskModeName(form.mode)}`),
          h('span', '已带入失败任务参数，确认后会按当前表单重新创建任务。')
        ]) : null,
        h('div', { class: 'mode-grid' }, modes.map(mode => h('button', {
          class: ['mode-option', form.mode === mode.key ? 'selected' : ''],
          onClick: () => applyMode(mode.key)
        }, [h('small', mode.badge), h('strong', mode.title), h('span', mode.note)]))),
        renderCapabilityPanel(activeCapability.value, activeModeCapability.value),
        h('div', { class: 'form-grid' }, [
          h('label', ['平台', h('select', { value: form.platform, onChange: (event: Event) => form.platform = (event.target as HTMLSelectElement).value }, [
            h('option', { value: 'dy' }, '抖音'),
            h('option', { value: 'xhs' }, '小红书'),
            h('option', { value: 'ks' }, '快手')
          ])]),
          h('label', ['登录方式', h('select', { value: form.login_type, onChange: (event: Event) => form.login_type = (event.target as HTMLSelectElement).value }, [
            h('option', { value: 'qrcode' }, '二维码'),
            h('option', { value: 'phone' }, '手机号'),
            h('option', { value: 'cookie' }, 'Cookie')
          ])]),
          h('label', { class: 'form-field field-full' }, ['关键词', h(TagInput, {
            modelValue: form.keyword_tags,
            disabled: !modeUsesKeywords.value,
            placeholder: modeUsesKeywords.value ? '输入关键词后按回车，例如：AI客服' : '账号/详情任务不使用关键词',
            'onUpdate:modelValue': (value: string[]) => form.keyword_tags = value
          })]),
          h('label', { class: 'form-field field-full' }, ['创作者主页/ID', h(TagInput, {
            modelValue: form.creator_id_tags,
            disabled: modeUsesKeywords.value,
            placeholder: modeNeedsCreator.value ? '输入账号主页或ID后按回车' : '详情任务可不填',
            'onUpdate:modelValue': (value: string[]) => form.creator_id_tags = value
          })]),
          h('label', { class: 'form-field field-full' }, ['指定内容ID/链接', h(TagInput, {
            modelValue: form.specified_id_tags,
            disabled: modeUsesKeywords.value,
            placeholder: modeUsesKeywords.value ? '搜索任务不使用内容ID' : '输入内容ID或链接后按回车',
            'onUpdate:modelValue': (value: string[]) => form.specified_id_tags = value
          })]),
          h('label', [modeUsesKeywords.value ? '内容数量（每个关键词上限）' : '内容数量（每个账号上限）', h('input', { type: 'number', value: form.content_count, min: 1, onInput: (event: Event) => form.content_count = Number((event.target as HTMLInputElement).value) })]),
          h('label', ['单条评论数', h('input', { type: 'number', value: form.comment_count, min: 0, onInput: (event: Event) => form.comment_count = Number((event.target as HTMLInputElement).value) })]),
          h('label', ['并发数', h('input', { type: 'number', value: form.max_concurrency, min: 1, max: 10, onInput: (event: Event) => form.max_concurrency = Number((event.target as HTMLInputElement).value) })])
        ]),
        h('div', { class: 'toggles' }, [
          h('label', [h('input', { type: 'checkbox', checked: form.collect_comments, onChange: (event: Event) => form.collect_comments = (event.target as HTMLInputElement).checked }), '采集评论']),
          h('label', [h('input', { type: 'checkbox', checked: form.collect_sub_comments, onChange: (event: Event) => form.collect_sub_comments = (event.target as HTMLInputElement).checked }), '二级评论']),
          h('label', [h('input', { type: 'checkbox', checked: form.headless, onChange: (event: Event) => form.headless = (event.target as HTMLInputElement).checked }), '无头模式']),
          h('label', [h('input', { type: 'checkbox', checked: form.execute_crawler, onChange: (event: Event) => form.execute_crawler = (event.target as HTMLInputElement).checked }), '立即运行'])
        ]),
        h('div', { class: 'action-row' }, [
          h('button', { class: 'primary-action', onClick: submit }, [h(VideoPlay, { class: 'inline-icon' }), prefillSource.value ? '按当前参数重新启动' : '开始采集并导入'])
        ]),
        renderTaskPreviewPanel(taskPreview.value, previewError.value, previewLoading.value)
      ])
      ],
      side: () => [
      h('aside', { class: 'pane side-pane' }, [
        h('div', { class: 'section-title' }, [h('h2', '最近任务'), h('span', '确认采集是否跑通')]),
        h('div', { class: 'task-list' }, (props.tasks as Dict[]).slice(0, 8).map(task => h('button', { class: 'task-row', onClick: () => emit('open-logs', task.id) }, [
          h('strong', `${task.id} · ${task.name}`),
          h('span', `${task.platform} / ${task.mode}`),
          h('em', { class: `status ${task.status}` }, task.status)
        ])))
      ])
      ]
    })
  }
})
