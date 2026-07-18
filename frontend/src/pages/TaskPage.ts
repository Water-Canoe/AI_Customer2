import { computed, defineComponent, h, reactive, ref, watch } from 'vue'
import type { Component } from 'vue'
import { onMounted } from 'vue'
import { Aim, ChatDotRound, Compass, Search, Tickets, User, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { Dict } from '../shared/types'
import { platformName, taskModeName } from '../shared/format'
import { api } from '../shared/api'
import { SplitPane } from '../components/ui/SplitPane'
import { TagInput, joinTags, splitTagText } from '../components/ui/TagInput'
import { iconBadge, sectionTitle, type WorkbenchTone } from '../components/ui/Workbench'

export default defineComponent({
  props: {
    tasks: { type: Array, required: true },
    settings: { type: Object, required: true },
    retryDraft: { type: Object, default: null }
  },
  emits: ['create-task', 'open-logs', 'consume-retry-draft'],
  setup(props, { emit }) {
    const modes: Array<{ key: string, title: string, note: string, badge: string, icon: Component, tone: WorkbenchTone }> = [
      { key: 'competitor_discovery', title: '竞品账号采集', note: '找候选竞品账号', badge: '找账号', icon: Aim, tone: 'teal' },
      { key: 'competitor_crawl', title: '竞品账号爬取', note: '从评论转线索', badge: '找线索', icon: User, tone: 'blue' },
      { key: 'demand_content', title: '找需求内容', note: '从内容找作者', badge: '找需求', icon: Search, tone: 'amber' },
      { key: 'own_account', title: '自家账号互动', note: '筛高意向用户', badge: '自有流量', icon: ChatDotRound, tone: 'green' }
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
      account_id: '',
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
    const accounts = ref<Dict[]>([])
    const settingsDefaultsApplied = ref(false)
    const modeNeedsCreator = computed(() => ['competitor_crawl', 'own_account'].includes(form.mode))
    const modeUsesKeywords = computed(() => ['competitor_discovery', 'demand_content'].includes(form.mode))
    const availableAccounts = computed(() => accounts.value.filter(account =>
      account.platform === form.platform && account.enabled && account.status === 'ready' && account.features?.includes('acquisition')
    ))
    function selectDefaultAccount() {
      if (availableAccounts.value.some(account => account.id === form.account_id)) return
      const preferred = availableAccounts.value.find(account => account.default_features?.includes('acquisition')) || availableAccounts.value[0]
      form.account_id = String(preferred?.id || '')
    }
    async function loadAccounts() {
      try {
        const { data } = await api.get('/accounts', { params: { feature: 'acquisition' } })
        accounts.value = data
        selectDefaultAccount()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '拓客账号加载失败')
      }
    }
    const creatorFieldLabel = computed(() => {
      if (form.mode === 'own_account') return '自家账号主页/ID'
      if (form.mode === 'competitor_crawl') return '竞品账号主页/ID'
      return '创作者主页/ID'
    })
    const creatorPlaceholder = computed(() => {
      if (form.mode === 'own_account') return '输入自家账号主页或ID后按回车'
      if (form.mode === 'competitor_crawl') return '输入竞品账号主页或ID后按回车'
      return modeNeedsCreator.value ? '输入账号主页或ID后按回车' : '详情任务可不填'
    })
    function configuredOwnAccounts(platform: string) {
      const raw = (props.settings as Dict)?.own_accounts
      let source = raw
      if (typeof raw === 'string') {
        try { source = JSON.parse(raw) } catch { source = {} }
      }
      const items = (source && typeof source === 'object') ? (source as Dict)[platform] : []
      return Array.isArray(items) ? items.map(item => String(item).trim()).filter(Boolean) : splitTagText(String(items || ''))
    }
    function applyOwnAccountDefaults() {
      form.creator_id_tags = configuredOwnAccounts(form.platform)
    }
    function applyMode(modeKey: string) {
      const previousMode = form.mode
      prefillSource.value = null
      form.mode = modeKey
      form.collect_comments = ['competitor_crawl', 'own_account'].includes(modeKey)
      form.collect_sub_comments = form.collect_comments
      if (['competitor_discovery', 'demand_content'].includes(modeKey)) {
        form.creator_id_tags = []
        form.specified_id_tags = []
      } else {
        form.keyword_tags = []
        if (modeKey === 'own_account') {
          applyOwnAccountDefaults()
        } else if (previousMode === 'own_account') {
          form.creator_id_tags = []
        }
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
      form.account_id = String(task.account_id || '')
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
        account_id: form.account_id,
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
    watch(
      () => props.settings,
      () => {
        applySettingsDefaultsOnce()
        if (form.mode === 'own_account' && !form.creator_id_tags.length) applyOwnAccountDefaults()
      },
      { immediate: true, deep: true }
    )
    watch(
      () => [form.mode, form.platform],
      () => {
        if (form.mode === 'own_account') applyOwnAccountDefaults()
        selectDefaultAccount()
      }
    )
    onMounted(loadAccounts)
    watch(
      () => props.retryDraft,
      draft => {
        if (!draft) return
        applyTaskDraft(draft as Dict)
        emit('consume-retry-draft')
      },
      { immediate: true }
    )
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
      if (payload.execute_crawler && !payload.account_id) {
        ElMessage.error(`请先到账号中心配置并登录${platformName(payload.platform)}拓客账号`)
        return
      }
      emit('create-task', payload)
      prefillSource.value = null
    }
    return () => h(SplitPane, { storageKey: 'tasks', side: 'right', defaultSideWidth: 360 }, {
      default: () => [
      h('section', { class: 'pane primary-pane' }, [
        sectionTitle({ title: '选择拓客模式', subtitle: '先选目标，再填必要参数', icon: Compass, tone: 'teal' }),
        prefillSource.value ? h('div', { class: 'retry-prefill' }, [
          h('strong', `重试 ${prefillSource.value.id || ''} · ${taskModeName(form.mode)}`),
          h('span', '已带入失败任务参数，确认后会按当前表单重新创建任务。')
        ]) : null,
        h('div', { class: 'mode-grid' }, modes.map(mode => h('button', {
          class: ['mode-option', form.mode === mode.key ? 'selected' : ''],
          onClick: () => applyMode(mode.key)
        }, [
          h('div', { class: 'mode-option-head' }, [
            iconBadge(mode.icon, mode.tone),
            h('div', { class: 'mode-option-copy' }, [
              h('small', mode.badge),
              h('strong', mode.title)
            ])
          ]),
          h('span', mode.note)
        ]))),
        h('div', { class: 'form-grid' }, [
          h('label', ['平台', h('select', { value: form.platform, onChange: (event: Event) => form.platform = (event.target as HTMLSelectElement).value }, [
            h('option', { value: 'dy' }, '抖音'),
            h('option', { value: 'xhs' }, '小红书'),
            h('option', { value: 'ks' }, '快手')
          ])]),
          h('label', ['执行账号', h('select', { value: form.account_id, onChange: (event: Event) => form.account_id = (event.target as HTMLSelectElement).value }, [
            h('option', { value: '' }, availableAccounts.value.length ? '请选择账号' : '账号中心暂无可用账号'),
            ...availableAccounts.value.map(account => h('option', { value: account.id }, account.name))
          ])]),
          h('label', { class: 'form-field field-full' }, ['关键词', h(TagInput, {
            modelValue: form.keyword_tags,
            disabled: !modeUsesKeywords.value,
            placeholder: modeUsesKeywords.value ? '输入关键词后按回车，例如：AI客服' : '账号/详情任务不使用关键词',
            'onUpdate:modelValue': (value: string[]) => form.keyword_tags = value
          })]),
          h('label', { class: 'form-field field-full' }, [creatorFieldLabel.value, h(TagInput, {
            modelValue: form.creator_id_tags,
            disabled: modeUsesKeywords.value,
            placeholder: creatorPlaceholder.value,
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
        ])
      ])
      ],
      side: () => [
      h('aside', { class: 'pane side-pane' }, [
        sectionTitle({ title: '最近任务', subtitle: '确认采集是否跑通', icon: Tickets, tone: 'blue' }),
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
