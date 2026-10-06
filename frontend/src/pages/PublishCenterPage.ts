import { computed, defineComponent, h, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Promotion } from '@element-plus/icons-vue'
import { api } from '../shared/api'
import { isAccountFeatureReady } from '../shared/accounts'
import type { Dict, PlatformAccount } from '../shared/types'
import { createAutoSyncController } from '../composables/autoSync'
import { usePersistentDraft, draftStatusLabel } from '../shared/usePersistentDraft'
import { buildPublishRequest, publishSourceKey, publishTextLength, PUBLISH_LIMITS, validatePublishCopy } from '../components/content/publishModel'
import { emptyState } from '../components/ui/Workbench'
import { DEFAULT_PAGE_SIZE, ListPagination, type PageChange } from '../components/ui/ListPagination'
import './content.css'

const emptyComposer = (): Dict => ({ source: null, title: '', description: '', tags: '', account_ids: [], account_overrides: {}, publish_strategy: 'immediate', scheduled_at: '' })

export default defineComponent({
  name: 'PublishCenterPage',
  props: { refreshSeq: { type: Number, default: 0 } },
  setup(props) {
    const route = useRoute()
    const router = useRouter()
    const accounts = ref<PlatformAccount[]>([])
    const tasks = ref<Dict>({ items: [], total: 0, page: 1, page_size: DEFAULT_PAGE_SIZE })
    const focusedTask = ref<Dict | null>(null)
    const savedDrafts = ref<Dict>({ items: [], total: 0, page: 1, page_size: DEFAULT_PAGE_SIZE })
    const draftSearch = ref('')
    const taskStatus = ref('')
    const composer = ref<Dict>(emptyComposer())
    const composerOpen = ref(false)
    const composerError = ref('')
    const sourceItems = ref<Dict[]>([])
    const draftKey = ref(String(route.query.draft_id || crypto.randomUUID()))
    const loading = ref(false)
    const restoringComposer = ref(false)
    let loadedComposerKey = ''
    let pageActive = true
    const persistentDraft = usePersistentDraft({ kind: 'publish', key: draftKey,
      getPayload: () => composer.value, applyPayload: payload => composer.value = { ...emptyComposer(), ...payload } })
    const stats = computed(() => tasks.value.summary || { pending: 0, running: 0, succeeded: 0, review: 0, active: 0 })
    const sourceCount = computed(() => composer.value.source?.type === 'video_output' ? composer.value.source.output_ids.length : composer.value.source ? 1 : 0)
    const selectedAccounts = computed(() => accounts.value.filter(account => composer.value.account_ids.includes(account.id)))
    const taskCount = computed(() => sourceCount.value * composer.value.account_ids.length)
    const submitted = computed(() => Boolean(composer.value.return_context?.selection?.length))

    const refresh = createAutoSyncController({ interval: () => 3000, sync: async reason => {
      if (reason === 'route' || Number(stats.value.active || 0) || accounts.value.some(account => account.login_status?.creator?.status === 'checking')) {
        await loadAll(reason === 'route')
        if (reason === 'route' && pageActive) await openComposerFromRoute()
      }
    } })
    onMounted(() => { refresh.start(); void refresh.trigger('route') })
    onUnmounted(() => { pageActive = false; refresh.stop() })
    watch(() => props.refreshSeq, () => void refresh.trigger('route'))
    watch(() => route.fullPath, () => void refresh.trigger('route'))
    onBeforeRouteLeave(() => !restoringComposer.value)
    onBeforeRouteUpdate(async to => {
      // 同组件切换也须完成素材读取；写入失败时保留当前编辑器。
      if (restoringComposer.value) return false
      if (to?.query.draft_id === draftKey.value) return true
      try { await persistentDraft.flush(); return true } catch { return false }
    })

    function accountUrl() { return { path: '/accounts', query: { return_to: route.fullPath } } }
    async function loadAll(showError = true) {
      try {
        const results = await Promise.all([
          api.get<PlatformAccount[]>('/accounts', { params: { feature: 'publish' } }),
          api.get('/content/publish-tasks', { params: { page: tasks.value.page, page_size: tasks.value.page_size, status: taskStatus.value } }),
        ])
        accounts.value = results[0].data
        tasks.value = results[1].data
        if (route.query.task && pageActive) {
          const { data } = await api.get('/content/publish-tasks/' + encodeURIComponent(String(route.query.task)))
          focusedTask.value = data
        } else focusedTask.value = null
        if (showError && pageActive) {
          await loadSavedDrafts()
        }
      } catch (error: any) { if (showError) ElMessage.error(error?.response?.data?.detail || '发布中心加载失败') }
    }

    async function loadSavedDrafts() {
      const params = { page: savedDrafts.value.page, page_size: savedDrafts.value.page_size, search: draftSearch.value }
      try {
        const { data } = await api.get('/drafts/publish', { params })
        if (pageActive && params.page === savedDrafts.value.page && params.page_size === savedDrafts.value.page_size && params.search === draftSearch.value) savedDrafts.value = data
      } catch (error: any) { ElMessage.error(error?.response?.data?.detail || '发布草稿查询失败') }
    }

    function searchSavedDrafts() { savedDrafts.value.page = 1; void loadSavedDrafts() }

    async function openComposerFromRoute() {
      const outputIds = String(route.query.output_ids || '').split(',').filter(Boolean)
      const assetIds = String(route.query.asset_ids || '').split(',').filter(Boolean)
      const requestedKey = String(route.query.draft_id || '')
      if (!outputIds.length && !assetIds.length && !requestedKey) { composerOpen.value = false; return }
      if (requestedKey && requestedKey === loadedComposerKey) { composerOpen.value = true; return }
      if (restoringComposer.value) return
      const requestedSource = outputIds.length ? { type: 'video_output', output_ids: outputIds } : { type: 'assets', asset_ids: assetIds }
      if (!requestedKey && composerOpen.value && composer.value.source && publishSourceKey(requestedSource) === publishSourceKey(composer.value.source)) return
      const requestedPath = route.fullPath
      const nextKey = requestedKey || (loadedComposerKey ? crypto.randomUUID() : draftKey.value)
      const retryingCurrent = Boolean(composerError.value && nextKey === draftKey.value)
      const ownsRestore = () => pageActive && route.name === 'content-publish' && route.fullPath === requestedPath && draftKey.value === nextKey
      restoringComposer.value = true
      composerError.value = ''
      try {
        if (loadedComposerKey) await persistentDraft.flush()
        if (!pageActive || route.fullPath !== requestedPath) return
        draftKey.value = nextKey
        if (!retryingCurrent) composer.value = emptyComposer()
        composerOpen.value = false
        const record = await persistentDraft.load()
        if (!ownsRestore()) return
        if (!record.exists) {
          composer.value.source = outputIds.length ? { type: 'video_output', output_ids: outputIds } : { type: 'assets', asset_ids: assetIds }
          composer.value.account_ids = accounts.value.filter(account => account.default_features.includes('publish') && isAccountFeatureReady(account, 'publish')).map(account => account.id)
        }
        if (!composer.value.source) throw new Error('发布草稿没有成品或素材，请从作品库重新选择')
        if (submitted.value) {
          // 已提交草稿只用于定位结果与清理，不依赖仍然存在的原始素材。
          sourceItems.value = []
          composerOpen.value = true
          loadedComposerKey = nextKey
          return
        }
        const source = composer.value.source
        const items = await Promise.all((source.type === 'video_output' ? source.output_ids : source.asset_ids).map(async (id: string) => {
          const { data } = await api.get(source.type === 'video_output' ? '/content/outputs/' + id : '/content/assets/' + id)
          return data
        }))
        if (!ownsRestore()) return
        sourceItems.value = items
        if (!record.exists) {
          const first = sourceItems.value[0]
          composer.value.title = first?.subject || String(first?.name || '').replace(/\.[^.]+$/, '')
          composer.value.description = first?.script || ''
        }
        composerOpen.value = true
        loadedComposerKey = nextKey
        restoringComposer.value = false
        if (!requestedKey) await router.replace({ path: '/content-publish', query: { ...route.query, draft_id: nextKey } })
      } catch (error: any) {
        if (ownsRestore()) { composerError.value = error?.response?.data?.detail || error?.message || '发布草稿读取失败'; ElMessage.error(composerError.value) }
      } finally { restoringComposer.value = false }
    }

    function accountCopy(id: string): Dict { return composer.value.account_overrides[id] || composer.value }
    function changeAccountCopy(id: string, field: string, value: unknown) {
      // 首次修改时才复制统一文案；未修改的账号会继续跟随统一文案。
      if (!composer.value.account_overrides[id]) {
        const { title, description, tags, publish_strategy, scheduled_at } = composer.value
        composer.value.account_overrides[id] = { title, description, tags, publish_strategy, scheduled_at, options: {} }
      }
      composer.value.account_overrides[id][field] = value
    }
    function validationMessage(): string {
      if (!composer.value.source || !sourceCount.value) return '请选择发布成品或素材'
      if (!composer.value.account_ids.length) return '请选择至少一个发布账号'
      for (const id of composer.value.account_ids) {
        const account = accounts.value.find(item => item.id === id)
        if (!account || !isAccountFeatureReady(account, 'publish')) return '所选账号已停用或创作者登录失效，请先准备账号'
        const problem = validatePublishCopy(accountCopy(id))
        if (problem) return account.name + '：' + problem
        if (account.platform === 'dy' && composer.value.source.type === 'assets' && sourceItems.value.filter(item => item.asset_type === 'image').length > PUBLISH_LIMITS.douyinImages) return '抖音图文最多支持 35 张图片'
      }
      const types = new Set(sourceItems.value.map(item => item.asset_type))
      if (composer.value.source.type === 'assets' && !(types.size === 1 && (types.has('image') || types.has('video') && sourceItems.value.length === 1))) return '只能发布一个视频，或一组图片'
      return ''
    }
    async function generateMetadata(id?: string) {
      if (submitted.value) return
      const copy = id ? accountCopy(id) : composer.value
      if (!String(copy.title || '').trim()) return ElMessage.warning('请先填写标题')
      loading.value = true
      try {
        const platform = id ? accounts.value.find(account => account.id === id)?.platform : selectedAccounts.value[0]?.platform
        const { data } = await api.post('/content/social-metadata', { video_subject: copy.title, video_script: copy.description, language: 'zh-CN', platform: platform || 'dy' })
        if (id) {
          changeAccountCopy(id, 'title', data.title || copy.title)
          changeAccountCopy(id, 'description', data.caption || copy.description)
          changeAccountCopy(id, 'tags', (data.hashtags || []).join(', '))
        } else {
          composer.value.title = data.title || composer.value.title
          composer.value.description = data.caption || composer.value.description
          composer.value.tags = (data.hashtags || []).join(', ')
        }
      } catch (error: any) { ElMessage.error(error?.response?.data?.detail || '发布文案生成失败') }
      finally { loading.value = false }
    }
    async function submitComposer() {
      if (submitted.value) return
      const problem = validationMessage()
      if (problem) return ElMessage.warning(problem)
      loading.value = true
      let created = false
      try {
        await persistentDraft.flush()
        const { data } = await api.post<Dict[]>('/content/publish-tasks', { ...buildPublishRequest(composer.value), draft_key: draftKey.value })
        created = true
        composer.value.return_context = { route: '/content-publish', query: { task: data[0]!.id }, selection: data.map(task => String(task.id)) }
        ElMessage.success('已创建 ' + taskCount.value + ' 条发布任务，按账号逐条执行')
        await finishPublishSubmission()
      } catch (error: any) { ElMessage.error(error?.response?.data?.detail || (created ? '任务已创建，但草稿清理失败，请勿重复提交' : '发布任务创建失败')) }
      finally { loading.value = false }
    }
    async function finishPublishSubmission() {
      await persistentDraft.clear()
      composerOpen.value = false
      loadedComposerKey = ''
      composer.value = emptyComposer()
      await router.replace('/content-publish')
    }
    async function retryPublishCleanup() {
      loading.value = true
      try { await finishPublishSubmission() }
      catch { ElMessage.error('发布任务已创建，草稿仍未清理，可重试清理或查看任务') }
      finally { loading.value = false }
    }
    async function taskAction(task: Dict, action: 'cancel' | 'retry') {
      try {
        if (action === 'retry' && task.status === 'review_required') {
          await ElMessageBox.confirm('请先在平台确认这条内容没有发布成功。继续会再次提交，可能产生重复内容。', '核对平台结果', { confirmButtonText: '已确认未发布，重试', type: 'warning' })
          await api.patch('/content/publish-tasks/' + task.id + '/mark-result', { status: 'failed', note: '人工在平台核对未发布，允许重试' })
        }
        await api.post('/content/publish-tasks/' + task.id + '/' + action)
        ElMessage.success(action === 'retry' ? '发布任务已重新排队' : '取消请求已发送')
        await loadAll()
      } catch (error: any) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.response?.data?.detail || '发布操作失败') }
    }
    async function markPublished(task: Dict) {
      try {
        await ElMessageBox.confirm('确认已在对应平台查看并找到这条已发布内容？', '人工确认发布结果', { confirmButtonText: '确认已发布' })
        await api.patch('/content/publish-tasks/' + task.id + '/mark-result', { status: 'succeeded', note: '人工在平台核对后确认已发布' })
        await loadAll()
      } catch (error: any) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.response?.data?.detail || '人工确认失败') }
    }
    async function editFailedTask(task: Dict) {
      const key = crypto.randomUUID()
      try {
        const { data } = await api.get('/content/publish-tasks/' + task.id)
        const source = data.source_type === 'video_output' ? { type: 'video_output', output_ids: [data.output_id] } : { type: 'assets', asset_ids: data.assets.filter((item: Dict) => item.role === 'media').map((item: Dict) => item.id) }
        await api.put('/drafts/publish/' + key, { payload: { ...emptyComposer(), source, title: data.title, description: data.description, tags: data.tags.join(', '), account_ids: [data.account_id], publish_strategy: data.publish_strategy, scheduled_at: data.scheduled_at || '' } })
        await router.push({ path: '/content-publish', query: { draft_id: key } })
      } catch (error: any) { ElMessage.error(error?.response?.data?.detail || '无法建立重发草稿') }
    }

    function copyFields(copy: Dict, update: (field: string, value: string) => void) {
      return h('div', { class: 'publish-copy-grid' }, [
        field('标题', copy.title, value => update('title', value), false, PUBLISH_LIMITS.title),
        field('标签', Array.isArray(copy.tags) ? copy.tags.join(', ') : copy.tags, value => update('tags', value)),
        field('正文', copy.description, value => update('description', value), true, PUBLISH_LIMITS.description),
        h('label', { class: 'publish-field' }, [h('span', '发布方式'), h('select', { value: copy.publish_strategy, onChange: (event: Event) => update('publish_strategy', (event.target as HTMLSelectElement).value) }, [h('option', { value: 'immediate' }, '立即发布'), h('option', { value: 'scheduled' }, '平台定时发布')])]),
        copy.publish_strategy === 'scheduled' ? h('label', { class: 'publish-field' }, [h('span', '发布时间（至少提前 2 小时）'), h('input', { type: 'datetime-local', value: String(copy.scheduled_at || '').replace(' ', 'T'), onInput: (event: Event) => update('scheduled_at', (event.target as HTMLInputElement).value) })]) : null,
      ])
    }
    function renderComposer() {
      if (!composerOpen.value) return null
      return h('section', { class: 'workspace-panel publish-composer' }, [
        h('div', { class: 'workspace-heading' }, [h('div', [h('h2', '准备发布'), h('p', { class: 'workspace-note' }, '选择账号并确认文案，提交后每个账号独立记录结果。')]), h('div', { class: 'workspace-toolbar' }, [h('button', { class: 'secondary-action', onClick: () => router.push('/content-publish') }, '返回发布草稿'), h('button', { class: 'secondary-action', onClick: () => router.push('/content-records') }, '返回作品库')])]),
        h('div', { class: 'publish-source-strip' }, sourceItems.value.map(item => h('span', String(item.subject || item.name) + (item.attempt ? ' · 第 ' + item.attempt + ' 次成品' : '')))),
        h('div', { class: 'workspace-heading' }, [h('h3', '1. 发布账号'), h('button', { class: 'secondary-action', onClick: () => router.push(accountUrl()) }, '准备账号')]),
        h('div', { class: 'publish-account-options' }, accounts.value.map(account => h('label', [h('input', { type: 'checkbox', disabled: !isAccountFeatureReady(account, 'publish'), checked: composer.value.account_ids.includes(account.id), onChange: (event: Event) => { composer.value.account_ids = (event.target as HTMLInputElement).checked ? [...new Set([...composer.value.account_ids, account.id])] : composer.value.account_ids.filter((id: string) => id !== account.id) } }), platformLabel(account.platform) + ' · ' + account.name + (isAccountFeatureReady(account, 'publish') ? '' : '（需登录）')]))),
        !accounts.value.length ? h('p', { class: 'workspace-note' }, '还没有发布账号。点击准备账号，完成创作者登录后可返回这个草稿。') : null,
        h('h3', '2. 统一文案'),
        copyFields(composer.value, (name, value) => composer.value[name] = value),
        h('div', { class: 'workspace-toolbar' }, [h('button', { class: 'secondary-action', disabled: loading.value || submitted.value, onClick: () => generateMetadata() }, 'AI生成发布文案'), h('span', { class: 'workspace-note' }, '最多 5 个标签；超出字数时会提示，不会截断文案。')]),
        selectedAccounts.value.map(account => h('details', { class: 'publish-account-editor' }, [
          h('summary', platformLabel(account.platform) + ' · ' + account.name + (composer.value.account_overrides[account.id] ? ' · 已单独编辑' : ' · 使用统一文案')),
          copyFields(accountCopy(account.id), (name, value) => changeAccountCopy(account.id, name, value)),
          h('div', { class: 'workspace-toolbar' }, [h('button', { class: 'secondary-action', disabled: loading.value || submitted.value, onClick: () => generateMetadata(account.id) }, '为该账号生成文案'), composer.value.account_overrides[account.id] ? h('button', { class: 'text-icon-button', onClick: () => delete composer.value.account_overrides[account.id] }, '恢复统一文案') : null]),
          validatePublishCopy(accountCopy(account.id)) ? h('p', { class: 'publish-validation' }, validatePublishCopy(accountCopy(account.id))) : null,
        ])),
        h('div', { class: 'publish-submit-summary' }, [
          h('div', [h('strong', sourceCount.value + ' 份内容 × ' + composer.value.account_ids.length + ' 个账号 = ' + taskCount.value + ' 条发布任务'), h('p', { class: 'workspace-note' }, submitted.value ? '此草稿已创建发布任务，不会重复提交。' : persistentDraft.error.value || draftStatusLabel(persistentDraft.status.value)), !submitted.value && persistentDraft.error.value ? h('button', { class: 'text-icon-button', onClick: () => persistentDraft.flush().catch(() => undefined) }, '重试保存草稿') : null, !submitted.value && validationMessage() ? h('p', { class: 'publish-validation' }, validationMessage()) : null]),
          submitted.value ? h('div', { class: 'workspace-toolbar' }, [h('button', { class: 'secondary-action', onClick: () => router.push({ path: '/content-publish', query: composer.value.return_context.query }) }, '查看已创建发布任务'), h('button', { class: 'primary-action', disabled: loading.value, onClick: retryPublishCleanup }, '重试清理已提交草稿')])
            : h('button', { class: 'primary-action', disabled: loading.value, onClick: submitComposer }, loading.value ? '提交中…' : '确认创建发布任务'),
        ]),
      ])
    }
    function renderTasks() {
      return h('section', { class: 'workspace-panel' }, [
        h('div', { class: 'workspace-heading' }, [h('div', [h('h2', '发布任务'), h('p', { class: 'workspace-note' }, '共 ' + tasks.value.total + ' 条 · 发布结果不明确时请先到平台核对')]), h('div', { class: 'workspace-toolbar' }, [h('button', { class: 'secondary-action', onClick: () => router.push(accountUrl()) }, '管理发布账号'), h('button', { class: 'secondary-action', onClick: () => loadAll() }, '刷新')])]),
        h('div', { class: 'publish-stat-grid' }, [statCard('待发布', stats.value.pending), statCard('发布中', stats.value.running), statCard('成功', stats.value.succeeded), statCard('需要处理', stats.value.review)]),
        h('div', { class: 'workspace-toolbar', style: { marginBottom: '18px' } }, [h('select', { value: taskStatus.value, onChange: (event: Event) => { taskStatus.value = (event.target as HTMLSelectElement).value; tasks.value.page = 1; void loadAll() } }, [h('option', { value: '' }, '全部状态'), ...['waiting_media', 'queued', 'running', 'succeeded', 'failed', 'review_required', 'cancelled'].map(value => h('option', { value }, taskStatusLabel(value)))])]),
        (tasks.value.items || []).length ? h('div', { class: 'publish-task-list' }, tasks.value.items.map((task: Dict) => renderTaskCard(task))) : emptyState({ title: '暂无发布任务', description: '从作品库选择成品，或从素材库选择视频与图片发起发布。', icon: Promotion }),
        h(ListPagination, { page: tasks.value.page || 1, pageSize: tasks.value.page_size || DEFAULT_PAGE_SIZE, total: tasks.value.total || 0, onChange: (value: PageChange) => { tasks.value.page = value.page; tasks.value.page_size = value.page_size; void loadAll() } }),
      ])
    }
    function renderTaskCard(task: Dict) {
      return h('article', { class: 'publish-task-card' }, [
          h('div', { class: 'publish-platform-mark platform-' + task.platform }, platformLabel(task.platform).slice(0, 1)),
          h('div', { class: 'publish-task-copy' }, [h('strong', task.title), h('span', platformLabel(task.platform) + ' · ' + task.account_name), h('small', taskStageLabel(task.current_stage) + ' · ' + (task.progress || 0) + '% · ' + (task.scheduled_at || task.created_at)), task.error ? h('p', { class: 'content-job-error' }, task.error) : null,
            task.source_type === 'video_output' && !task.output_id && ['failed', 'cancelled'].includes(task.status) ? h('p', { class: 'workspace-note' }, '生成未完成，请先恢复视频生成。') : null]),
          h('span', { class: 'status-pill status-' + task.status }, taskStatusLabel(task.status)),
          h('div', { class: 'task-card-actions' }, [
            focusedTask.value?.id !== task.id ? h('button', { class: 'secondary-action', onClick: () => router.push({ path: '/content-publish', query: { ...route.query, task: task.id } }) }, '查看详情') : null,
            ['waiting_media', 'queued', 'running'].includes(task.status) ? h('button', { class: 'danger-soft', onClick: () => taskAction(task, 'cancel') }, '取消') : null,
            ['failed', 'review_required', 'cancelled'].includes(task.status) && (task.source_type !== 'video_output' || task.output_id) ? h('button', { class: 'primary-soft', onClick: () => taskAction(task, 'retry') }, task.status === 'review_required' ? '确认未发布后重试' : '重试') : null,
            ['failed', 'cancelled'].includes(task.status) && (task.source_type !== 'video_output' || task.output_id) ? h('button', { class: 'secondary-action', onClick: () => editFailedTask(task) }, '修改后重发') : null,
            task.source_type === 'video_output' && !task.output_id && task.video_job_id ? h('button', { class: 'secondary-action', onClick: () => router.push({ path: '/content-records', query: { job: task.video_job_id } }) }, '查看生成') : null,
            task.status === 'review_required' ? h('button', { class: 'secondary-action', onClick: () => markPublished(task) }, '核对后标记已发布') : null,
          ]),
      ])
    }
    return () => h('div', { class: 'workspace-page content-product' }, [
      renderComposer(),
      focusedTask.value ? h('section', { class: 'workspace-panel publish-task-detail', 'aria-label': '发布任务详情' }, [
        h('div', { class: 'workspace-heading' }, [h('h2', '这条发布任务'), h('button', { class: 'secondary-action', onClick: () => { const query = { ...route.query }; delete query.task; focusedTask.value = null; void router.replace({ path: '/content-publish', query }) } }, '返回全部任务')]),
        renderTaskCard(focusedTask.value),
        h('p', { class: 'publish-confirmed-copy' }, focusedTask.value.description || '未填写正文'),
        h('p', { class: 'workspace-note' }, (focusedTask.value.tags || []).map((tag: string) => '#' + tag).join(' ')),
        h('p', { class: 'workspace-note' }, focusedTask.value.output_name ? '使用成品：' + focusedTask.value.output_name : (focusedTask.value.assets || []).map((asset: Dict) => asset.name).join('、') || '等待视频生成'),
        focusedTask.value.video_job_id ? h('button', { class: 'secondary-action', onClick: () => router.push({ path: '/content-records', query: { job: focusedTask.value!.video_job_id } }) }, '查看对应生成记录') : null,
      ]) : null,
      composerError.value && !composerOpen.value ? h('section', { class: 'workspace-panel' }, [h('p', { class: 'publish-validation' }, composerError.value), h('button', { class: 'secondary-action', disabled: restoringComposer.value, onClick: openComposerFromRoute }, '重试读取发布草稿')]) : null,
      !composerOpen.value ? h('section', { class: 'workspace-panel publish-draft-library' }, [
        h('h3', '发布草稿与提交结果'),
        h('div', { class: 'workspace-toolbar' }, [h('input', { placeholder: '搜索全部发布草稿', value: draftSearch.value, onInput: (event: Event) => draftSearch.value = (event.target as HTMLInputElement).value, onKeyup: (event: KeyboardEvent) => { if (event.key === 'Enter') searchSavedDrafts() } }), h('button', { class: 'secondary-action', onClick: searchSavedDrafts }, '搜索草稿')]),
        h('div', { class: 'content-work-list' }, savedDrafts.value.items.map((draft: Dict) => h('article', { class: 'content-work-row' }, [
          h('div', [h('h3', draft.payload?.title || '未命名发布草稿'), h('p', { class: 'workspace-note' }, `更新于 ${draft.updated_at || '—'} · ${draft.payload?.account_ids?.length || 0} 个账号`)]),
          h('button', { class: 'secondary-action', disabled: restoringComposer.value, onClick: () => router.push({ path: '/content-publish', query: draft.payload?.return_context?.selection?.length ? draft.payload.return_context.query : { draft_id: draft.key } }) }, (draft.payload?.return_context?.selection?.length ? '查看已提交任务 · ' : '继续编辑 · ') + (draft.payload?.title || '未命名发布草稿')),
        ]))),
        !savedDrafts.value.items.length ? h('p', { class: 'workspace-note' }, draftSearch.value ? '没有匹配的发布草稿，请调整搜索。' : '暂无发布草稿，从作品库选择成品即可开始。') : null,
        h(ListPagination, { page: savedDrafts.value.page, pageSize: savedDrafts.value.page_size, total: savedDrafts.value.total, onChange: (value: PageChange) => { savedDrafts.value.page = value.page; savedDrafts.value.page_size = value.page_size; void loadSavedDrafts() } }),
      ]) : null,
      renderTasks(),
    ])
  },
})

function field(label: string, value: unknown, update: (value: string) => void, multiline = false, maximum?: number) {
  return h('label', { class: ['publish-field', multiline ? 'field-full' : ''] }, [h('span', label), h(multiline ? 'textarea' : 'input', { value: String(value || ''), rows: multiline ? 4 : undefined, onInput: (event: Event) => update((event.target as HTMLInputElement).value) }), maximum ? h('small', { class: 'publish-copy-count' }, publishTextLength(value) + ' / ' + maximum) : null])
}
function platformLabel(value: string) { return ({ dy: '抖音', ks: '快手', xhs: '小红书' } as Dict)[value] || value }
function taskStatusLabel(value: string) { return ({ waiting_media: '等待成品', queued: '待发布', running: '发布中', succeeded: '已发布', failed: '失败', review_required: '需要核对', cancelled: '已取消' } as Dict)[value] || value }
function taskStageLabel(value: string) { return ({ waiting_media: '等待视频生成', queued: '等待执行', preparing: '准备素材', uploading: '上传素材', publishing: '提交发布', completed: '发布完成', review_required: '等待人工核对', failed: '发布失败', cancelled: '已取消' } as Dict)[value] || value }
function statCard(label: string, value: number) { return h('article', [h('span', label), h('strong', String(value || 0))]) }
