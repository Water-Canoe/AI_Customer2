import { computed, defineComponent, h, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, Collection, MagicStick, Message, Operation, Promotion, User } from '@element-plus/icons-vue'
import { api } from '../shared/api'
import type { Dict, PlatformAccount, RuntimeJob } from '../shared/types'
import { isAccountFeatureReady } from '../shared/accounts'
import { runtimeKindLabel, runtimeStatusLabel } from '../components/runtime/RuntimeQueuePanel'

// 首页只读取现有摘要与少量记录，不维护第二套业务状态。
export default defineComponent({
  name: 'WorkspacePage',
  props: { refreshSeq: { type: Number, default: 0 } },
  setup(props) {
    const router = useRouter()
    const summaries = ref<Record<string, Dict>>({})
    const accounts = ref<PlatformAccount[]>([])
    const jobs = ref<RuntimeJob[]>([])
    const loading = ref(true)
    const error = ref('')
    let requestSeq = 0
    const ready = computed(() => accounts.value.some(account => isAccountFeatureReady(account, 'acquisition')))

    async function load() {
      const revision = ++requestSeq
      try {
        const scopes = ['lead', 'message', 'content', 'runtime']
        const results = await Promise.all([
          ...scopes.map(scope => api.get('/workbench/status', { params: { scope } })),
          api.get('/accounts'),
          api.get('/runtime/jobs', { params: { page: 1, page_size: 5 } }),
        ])
        if (revision !== requestSeq) return
        summaries.value = Object.fromEntries(scopes.map((scope, i) => [scope, results[i].data.metrics]))
        accounts.value = results[4].data
        jobs.value = results[5].data.items
        error.value = ''
      } catch (cause: any) {
        if (revision !== requestSeq) return
        error.value = cause?.response?.data?.detail || '工作台读取失败，已有数据已保留'
      } finally { if (revision === requestSeq) loading.value = false }
    }
    onMounted(() => void load())
    onBeforeUnmount(() => { requestSeq += 1 })
    watch(() => props.refreshSeq, () => void load())

    function action(title: string, detail: string, path: string, icon: any) {
      return h('button', { class: 'workspace-action-card', onClick: () => router.push(path) }, [
        h(icon, { class: 'workspace-action-icon' }), h('strong', title), h('span', detail), h(ArrowRight, { class: 'workspace-action-arrow' }),
      ])
    }
    function metric(label: string, value: unknown, path: string) {
      return h('button', { class: 'workspace-metric', onClick: () => router.push(path) }, [h('span', label), h('strong', loading.value || !Object.keys(summaries.value).length ? '—' : String(value ?? 0)), h('small', '查看详情 →')])
    }
    return () => h('div', { class: 'workspace-page home-workspace' }, [
      h('section', { class: 'workspace-welcome' }, [h('div', [h('span', { class: 'workspace-eyebrow' }, '今天，从一件事开始'), h('h2', '把线索、内容和后续行动接起来'), h('p', '选择下一步，或继续正在进行的工作。')])]),
      error.value ? h('div', { class: 'workspace-alert', role: 'alert' }, [error.value, h('button', { onClick: load }, '重新读取')]) : null,
      !loading.value && !error.value && !ready.value ? h('section', { class: 'workspace-onboarding' }, [
        h(User, { class: 'workspace-action-icon' }), h('div', [h('strong', '准备第一个采集账号'), h('p', '添加平台账号、完成用户登录并启用采集用途，即可开始获客。创作本地视频也可以直接开始。')]),
        h('button', { class: 'primary-action', onClick: () => router.push({ path: '/accounts', query: { feature: 'acquisition', return_to: '/acquisition?create=1' } }) }, '配置账号'),
      ]) : null,
      h('div', { class: 'workspace-action-grid' }, [
        action('开始获客', '选择来源，采集并筛选客户', '/acquisition?create=1', Operation),
        action('跟进客户', '查看证据、话术与跟进状态', '/message-workbench', Message),
        action('创作视频', '从素材和文案生成可预览成品', '/content-create', MagicStick),
        action('执行引流', '维护计划，查看互动结果', '/traffic-plans', Promotion),
      ]),
      h('section', { class: 'workspace-stack' }, [
        h('div', { class: 'workspace-heading' }, [h('h2', '需要继续的工作'), h('span', '点击进入对应列表')]),
        h('div', { class: 'workspace-metric-grid' }, [
          metric('等待筛选', summaries.value.lead?.ai_pending, '/ai'),
          metric('待联系客户', summaries.value.message?.pending_customers, '/message-workbench'),
          metric('未发布成品', summaries.value.content?.unpublished_outputs, '/content-records?tab=outputs'),
          metric('执行异常', Number(summaries.value.runtime?.failed || 0) + Number(summaries.value.runtime?.interrupted || 0), '/runtime'),
        ]),
      ]),
      h('section', { class: 'workspace-panel' }, [
        h('div', { class: 'workspace-heading' }, [h('h2', '最近的任务'), h('button', { class: 'text-icon-button', onClick: () => router.push('/runtime') }, '进入任务中心 →')]),
        jobs.value.length ? h('div', { class: 'workspace-recent-list' }, jobs.value.map(job => h('button', { class: 'workspace-recent-row', onClick: () => router.push(job.detail_url || '/runtime') }, [h('div', [h('strong', job.display_name || runtimeKindLabel(job.kind)), h('small', runtimeKindLabel(job.kind))]), h('span', { class: ['status', job.status] }, runtimeStatusLabel(job.status)), h(ArrowRight, { class: 'inline-icon' })])))
          : h('div', { class: 'workspace-home-empty' }, [h(Collection, { class: 'workspace-action-icon' }), h('p', loading.value ? '正在读取任务…' : '还没有运行任务，从上面的入口开始即可。')]),
      ]),
    ])
  },
})
