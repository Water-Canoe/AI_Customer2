import { computed, defineComponent, h, reactive, ref, watch } from 'vue'
import type { Component } from 'vue'
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { usePersistentDraft, draftStatusLabel } from '../shared/usePersistentDraft'
import { Aim, ChatDotRound, Compass, Search, User, VideoPlay } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { Dict, PlatformAccount, TaskRecord } from '../shared/types'
import { platformName, taskModeName } from '../shared/format'
import { api } from '../shared/api'
import { isAccountFeatureReady } from '../shared/accounts'
import '../styles/operations.css'
import { TagInput, joinTags, splitTagText } from '../components/ui/TagInput'
import { iconBadge, sectionTitle, type WorkbenchTone } from '../components/ui/Workbench'

export default defineComponent({
  props: {
    settings: { type: Object, required: true },
    submitting: { type: Boolean, default: false },
    retryDraft: { type: Object, default: null }
  },
  emits: ['create-task', 'open-logs', 'consume-retry-draft', 'change-task-page', 'already-created'],
  setup(props, { emit, expose }) {
    const route = useRoute()
    const router = useRouter()
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
    const createdTaskId = ref('')
    const persistent = usePersistentDraft({
      kind: 'crawl', key: 'new',
      getPayload: () => ({ ...form, return_context: { route: '/acquisition', query: { create: '1', ...(createdTaskId.value ? { task: createdTaskId.value } : {}) }, selection: [] } }),
      applyPayload: payload => {
        // 只回填当前表单字段，后台草稿不包含登录凭据。
        for (const key of Object.keys(form)) if (key in payload) (form as Dict)[key] = payload[key]
        settingsDefaultsApplied.value = true
        createdTaskId.value = String(payload.return_context?.query?.task || '')
        if (createdTaskId.value) emit('already-created', createdTaskId.value)
      },
    })
    expose({ clearDraft: persistent.clear, flushDraft: persistent.flush, draftStatus: persistent.status, markCreated: (id: string) => createdTaskId.value = id })
    async function openAccounts() {
      try {
        await persistent.flush()
        await router.push({ path: '/accounts', query: { feature: 'acquisition', platform: form.platform, return_to: '/acquisition?create=1' } })
      } catch { ElMessage.error('请先保存草稿后再配置账号') }
    }
    const prefillSource = ref<TaskRecord | null>(null)
    let draftReady = false
    let pendingRetry: TaskRecord | null = null
    const accounts = ref<PlatformAccount[]>([])
    const settingsDefaultsApplied = ref(false)
    const modeNeedsCreator = computed(() => ['competitor_crawl', 'own_account'].includes(form.mode))
    const modeUsesKeywords = computed(() => ['competitor_discovery', 'demand_content'].includes(form.mode))
    const availableAccounts = computed(() => accounts.value.filter(account =>
      account.platform === form.platform && isAccountFeatureReady(account, 'acquisition')
    ))
    function selectDefaultAccount() {
      if (availableAccounts.value.some(account => account.id === form.account_id)) return
      const preferred = availableAccounts.value.find(account => account.default_features?.includes('acquisition')) || availableAccounts.value[0]
      form.account_id = String(preferred?.id || '')
    }
    async function loadAccounts() {
      try {
        const { data } = await api.get<PlatformAccount[]>('/accounts', { params: { feature: 'acquisition' } })
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
    function changePlatform(value: string) {
      form.platform = value
      if (form.mode === 'own_account') applyOwnAccountDefaults()
      selectDefaultAccount()
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
    function applyTaskDraft(task: TaskRecord) {
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
        draft_key: 'new',
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
        if (form.mode === 'own_account' && !form.creator_id_tags.length) applyOwnAccountDefaults()
        selectDefaultAccount()
      }
    )
    async function restoreDraft() {
      try {
        const stored = await persistent.load(); draftReady = true
        // 已提交状态即使恢复时保留了本地输入，也必须阻止重复创建。
        if (stored?.submitted) {
          createdTaskId.value = String(stored.payload.return_context?.query?.task || '')
          emit('already-created', createdTaskId.value)
        }
        if (route.query.creator) {
          // 从筛选结果进入时明确创建评论采集，仍由用户确认后提交。
          applyMode('competitor_crawl')
          form.platform = String(route.query.platform || 'dy')
          form.creator_id_tags = splitTagText(String(route.query.creator))
          await router.replace({ path: '/acquisition', query: { create: '1' } })
        }
        if (pendingRetry) { applyTaskDraft(pendingRetry); pendingRetry = null; emit('consume-retry-draft') }
        await loadAccounts()
      }
      catch (error: any) { ElMessage.error(error?.response?.data?.detail || '采集草稿加载失败，请重试') }
    }
    onMounted(restoreDraft)
    watch(
      () => props.retryDraft,
      draft => {
        if (!draft) return
        if (!draftReady) { pendingRetry = draft as TaskRecord; return }
        applyTaskDraft(draft as TaskRecord)
        emit('consume-retry-draft')
      },
      { immediate: true }
    )
    function submit() {
      if (createdTaskId.value || props.submitting || !draftReady) return
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
    return () => h('section', { class: 'operations-create-form' }, [
        sectionTitle({ title: '选择采集目标', subtitle: '参数自动保存，可返回继续编辑', icon: Compass, tone: 'teal' }),
        h('div', { class: 'toolbar' }, [h('span', { class: 'note' }, draftStatusLabel(persistent.status.value)), h('button', { onClick: () => persistent.save().catch(() => undefined) }, '保存草稿'), h('button', { onClick: openAccounts }, '配置执行账号')]),
        persistent.error.value ? h('p', { class: 'operations-error' }, persistent.error.value) : null,
        !draftReady && persistent.status.value === 'error' ? h('button', { onClick: restoreDraft }, '重新读取草稿') : null,
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
          h('label', ['平台', h('select', { value: form.platform, onChange: (event: Event) => changePlatform((event.target as HTMLSelectElement).value) }, [
            h('option', { value: 'dy' }, '抖音'),
            h('option', { value: 'xhs' }, '小红书'),
            h('option', { value: 'ks' }, '快手')
          ])]),
          h('label', ['执行账号', h('select', { value: form.account_id, onChange: (event: Event) => form.account_id = (event.target as HTMLSelectElement).value }, [
            h('option', { value: '' }, availableAccounts.value.length ? '请选择账号' : '账号中心暂无可用账号'),
            ...availableAccounts.value.map(account => h('option', { value: account.id }, account.name))
          ])]),
          modeUsesKeywords.value ? h('label', { class: 'form-field field-full' }, ['关键词', h(TagInput, {
            modelValue: form.keyword_tags,
            disabled: !modeUsesKeywords.value,
            placeholder: modeUsesKeywords.value ? '输入关键词后按回车，例如：AI客服' : '账号/详情任务不使用关键词',
            'onUpdate:modelValue': (value: string[]) => form.keyword_tags = value
          })]) : null,
          !modeUsesKeywords.value ? h('label', { class: 'form-field field-full' }, [creatorFieldLabel.value, h(TagInput, {
            modelValue: form.creator_id_tags,
            disabled: modeUsesKeywords.value,
            placeholder: creatorPlaceholder.value,
            'onUpdate:modelValue': (value: string[]) => form.creator_id_tags = value
          })]) : null,
          !modeUsesKeywords.value ? h('label', { class: 'form-field field-full' }, ['指定内容ID/链接', h(TagInput, {
            modelValue: form.specified_id_tags,
            disabled: modeUsesKeywords.value,
            placeholder: modeUsesKeywords.value ? '搜索任务不使用内容ID' : '输入内容ID或链接后按回车',
            'onUpdate:modelValue': (value: string[]) => form.specified_id_tags = value
          })]) : null,
          h('label', [modeUsesKeywords.value ? '内容数量（每个关键词上限）' : '内容数量（每个账号上限）', h('input', { type: 'number', value: form.content_count, min: 1, onInput: (event: Event) => form.content_count = Number((event.target as HTMLInputElement).value) })]),
          form.collect_comments ? h('label', ['单条评论数', h('input', { type: 'number', value: form.comment_count, min: 0, onInput: (event: Event) => form.comment_count = Number((event.target as HTMLInputElement).value) })]) : null,

        ]),
        h('details', { class: 'operations-advanced' }, [h('summary', '高级采集选项'), h('label', ['并发数', h('input', { type: 'number', value: form.max_concurrency, min: 1, max: 10, onInput: (event: Event) => form.max_concurrency = Number((event.target as HTMLInputElement).value) })]), h('div', { class: 'toggles' }, [
          h('label', [h('input', { type: 'checkbox', checked: form.collect_comments, onChange: (event: Event) => form.collect_comments = (event.target as HTMLInputElement).checked }), '采集评论']),
          h('label', [h('input', { type: 'checkbox', checked: form.collect_sub_comments, onChange: (event: Event) => form.collect_sub_comments = (event.target as HTMLInputElement).checked }), '二级评论']),
          h('label', [h('input', { type: 'checkbox', checked: form.headless, onChange: (event: Event) => form.headless = (event.target as HTMLInputElement).checked }), '无头模式']),
          h('label', [h('input', { type: 'checkbox', checked: form.execute_crawler, onChange: (event: Event) => form.execute_crawler = (event.target as HTMLInputElement).checked }), '立即运行'])
        ])]),
        h('div', { class: 'action-row' }, [
          h('button', { class: 'primary-action', disabled: Boolean(createdTaskId.value) || props.submitting || ['loading', 'error'].includes(persistent.status.value), onClick: submit }, [h(VideoPlay, { class: 'inline-icon' }), createdTaskId.value ? '任务已创建' : props.submitting ? '正在创建…' : prefillSource.value ? '按当前参数重新启动' : '开始采集并导入'])
        ])
    ])
  }
})
