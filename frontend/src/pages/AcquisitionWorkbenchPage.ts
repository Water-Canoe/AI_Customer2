import { defineComponent, h, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElDialog, ElMessage } from 'element-plus'
import TaskPage from './TaskPage'
import LogsPage from './LogsPage'
import '../styles/operations.css'

// 采集表单按需打开，任务产出与诊断始终通过同一列表进入。
export default defineComponent({
  name: 'AcquisitionWorkbenchPage',
  props: {
    tasks: { type: Array, required: true },
    page: { type: Number, default: 1 }, pageSize: { type: Number, default: 20 }, total: { type: Number, default: 0 },
    settings: { type: Object, required: true }, retryDraft: { type: Object, default: null },
    selectedTask: { type: Object, default: null }, diagnostics: { type: Object, default: () => ({}) },
    dedupSummary: { type: Object, default: () => ({}) }, query: { type: String, default: '' },
  },
  emits: ['create-task', 'open-logs', 'consume-retry-draft', 'change-task-page', 'select-task', 'retry-task', 'cancel-task', 'archive-task', 'delete-task', 'change-task-query'],
  setup(props, { emit }) {
    const route = useRoute()
    const router = useRouter()
    const creating = ref(false)
    const submitting = ref(false)
    const createdTaskId = ref('')
    const cleanupError = ref('')
    let cleanupDraft: (() => Promise<unknown>) | undefined
    const form = ref<any>(null)
    watch(() => route.query.create, value => creating.value = String(value || '') === '1', { immediate: true })
    watch(() => props.retryDraft, value => { if (value) creating.value = true }, { immediate: true })
    async function created(task: { id: string }) {
      createdTaskId.value = String(task.id)
      form.value?.markCreated(task.id)
      cleanupDraft = form.value?.clearDraft
      try { await cleanupDraft?.(); cleanupError.value = ''; creating.value = false }
      catch {
        cleanupError.value = '任务已创建，草稿清理失败；不会再次创建任务。'
        try { await form.value?.flushDraft(); creating.value = false }
        catch { cleanupError.value += ' 已创建标识未能保存，请先重试清理。' }
        ElMessage.warning(cleanupError.value)
      }
    }
    async function retryCleanup() {
      if (submitting.value) return
      submitting.value = true
      try { await cleanupDraft?.(); cleanupError.value = ''; creating.value = false }
      catch { cleanupError.value = '任务已创建，草稿清理仍失败，请重试清理。' }
      finally { submitting.value = false }
    }
    function alreadyCreated(id: string) {
      createdTaskId.value = id
      cleanupError.value = '此草稿对应的任务已创建，请清理草稿后再新建。'
      cleanupDraft = form.value?.clearDraft
    }
    async function closeCreate() {
      if (submitting.value || form.value?.draftStatus === 'loading') return
      try {
        await form.value?.flushDraft()
        creating.value = false
        const { create, ...query } = route.query
        await router.replace({ path: '/acquisition', query })
      } catch { ElMessage.error('草稿保存失败，请先重试保存') }
    }
    return () => h('div', { class: 'workspace-page operations-page acquisition-workspace' }, [
      h('div', { class: 'toolbar acquisition-heading' }, [
        h('button', { class: 'primary-action', disabled: submitting.value || Boolean(cleanupError.value), onClick: () => router.replace({ path: '/acquisition', query: { ...route.query, create: '1' } }) }, '新建采集'),
        h('p', { class: 'note' }, '新建采集 → 确认产出 → AI筛选 → 客户跟进'),
      ]),
      cleanupError.value ? h('div', { class: 'panel toolbar' }, [h('span', { class: 'operations-error' }, `${cleanupError.value} 任务 ${createdTaskId.value}`), h('button', { disabled: submitting.value, onClick: retryCleanup }, '重试清理草稿'), h('button', { onClick: () => emit('open-logs', createdTaskId.value) }, '查看已创建任务')]) : null,
      h('details', { class: 'acquisition-data-tools' }, [h('summary', '数据维护'), h('div', { class: 'toolbar' }, [h('span', { class: 'note' }, '按对象维护内容、评论、竞品、线索与客户资料。'), h('button', { onClick: () => router.push('/tables') }, '打开数据表')])]),
      h(LogsPage, { tasks: props.tasks, selectedTask: props.selectedTask, diagnostics: props.diagnostics, dedupSummary: props.dedupSummary, page: props.page, pageSize: props.pageSize, total: props.total, query: props.query, onSelectTask: (id: string) => emit('select-task', id), onRetryTask: (task: object) => emit('retry-task', task), onCancelTask: (id: string) => emit('cancel-task', id), onArchiveTask: (id: string) => emit('archive-task', id), onDeleteTask: (id: string) => emit('delete-task', id), onChangeTaskPage: (value: object) => emit('change-task-page', value), onChangeTaskQuery: (value: string) => emit('change-task-query', value) }),
      h(ElDialog, { modelValue: creating.value, title: '新建采集', width: '900px', destroyOnClose: true, closeOnClickModal: false, 'onUpdate:modelValue': (value: boolean) => { if (!value && !submitting.value) void closeCreate() } }, () => creating.value ? h(TaskPage, { ref: form, settings: props.settings, retryDraft: props.retryDraft, submitting: submitting.value || Boolean(cleanupError.value), onAlreadyCreated: alreadyCreated, onCreateTask: (payload: object) => { if (submitting.value || cleanupError.value) return; submitting.value = true; emit('create-task', payload, created, () => submitting.value = false) }, onConsumeRetryDraft: () => emit('consume-retry-draft') }) : null),
    ])
  },
})
