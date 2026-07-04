import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { CaretRight, Close, DataLine, Promotion, Refresh, VideoCamera } from '@element-plus/icons-vue'

import { SplitPane } from '../components/ui/SplitPane'
import { emptyState, sectionTitle } from '../components/ui/Workbench'
import { api } from '../shared/api'
import type { Dict } from '../shared/types'

export default defineComponent({
  name: 'TrafficPage',
  setup() {
    const route = useRoute()
    const loading = ref(false)
    const dashboard = ref<Dict>({ summary: {}, campaigns: [], runs: [] })
    const assets = ref<Dict[]>([])
    const keywordOptions = ref<Dict[]>([])
    const targets = ref<Dict>({ rows: [], total: 0, page: 1, page_size: 30 })
    const selectedCampaignId = ref<number | null>(null)
    const form = reactive<Dict>({
      name: '',
      source_type: 'competitor',
      keyword: '',
      action_comment: true,
      action_image: false,
      image_asset_ids: [],
    })

    const isRandom = computed(() => route.name === 'traffic-random')
    const campaigns = computed(() => (dashboard.value.campaigns || []).filter((item: Dict) => isRandom.value ? item.mode === 'random' : item.mode === 'targeted'))
    const selectedCampaign = computed(() => campaigns.value.find((item: Dict) => Number(item.id) === selectedCampaignId.value) || null)
    const activeSourceType = computed(() => selectedCampaign.value?.source_type || form.source_type)
    const showBuildTargets = computed(() => !isRandom.value && activeSourceType.value !== 'search_keyword')
    const latestRun = computed(() => (dashboard.value.runs || [])[0] || null)

    async function loadAll() {
      loading.value = true
      try {
        const [dashboardResponse, assetsResponse, keywordsResponse] = await Promise.all([
          api.get('/traffic/dashboard'),
          api.get('/traffic/assets'),
          api.get('/traffic/keywords'),
        ])
        dashboard.value = dashboardResponse.data
        assets.value = assetsResponse.data || []
        keywordOptions.value = keywordsResponse.data || []
        if (!selectedCampaignId.value && campaigns.value.length) selectedCampaignId.value = Number(campaigns.value[0].id)
        if (selectedCampaignId.value) await loadTargets()
      } finally {
        loading.value = false
      }
    }

    async function loadTargets(page = 1) {
      if (!selectedCampaignId.value) return
      const { data } = await api.get(`/traffic/campaigns/${selectedCampaignId.value}/targets`, { params: { page, page_size: 30 } })
      targets.value = data
    }

    async function createCampaign() {
      try {
        const payload = {
          name: form.name,
          mode: isRandom.value ? 'random' : 'targeted',
          source_type: isRandom.value ? 'random_feed' : form.source_type,
          keyword: form.keyword,
          action_comment: Boolean(form.action_comment),
          action_image: Boolean(form.action_image),
          image_asset_ids: selectedAssetIds(form.image_asset_ids),
        }
        const { data } = await api.post('/traffic/campaigns', payload)
        selectedCampaignId.value = Number(data.id)
        ElMessage.success('引流计划已创建')
        await loadAll()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '创建引流计划失败')
      }
    }

    async function buildTargets() {
      if (!selectedCampaignId.value) return
      try {
        const { data } = await api.post(`/traffic/campaigns/${selectedCampaignId.value}/targets/build`, { limit: 100 })
        const duplicated = Number(data.duplicated || 0)
        ElMessage.success(`已生成 ${data.created || 0} 条，跳过 ${data.skipped || 0} 条${duplicated ? `，重复 ${duplicated} 条` : ''}`)
        await loadTargets()
        await loadAll()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '生成队列失败')
      }
    }

    async function startRun() {
      if (!selectedCampaignId.value) return
      try {
        await api.post('/traffic/runs', { campaign_id: selectedCampaignId.value })
        ElMessage.success('引流批次已启动')
        await loadAll()
        await loadTargets()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '启动引流失败')
      }
    }

    async function cancelRun(run: Dict) {
      try {
        await api.post(`/traffic/runs/${run.id}/cancel`)
        ElMessage.success('已请求停止引流批次')
        await loadAll()
        await loadTargets()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '停止失败')
      }
    }

    onMounted(loadAll)

    return () => h(SplitPane, { storageKey: isRandom.value ? 'traffic-random' : 'traffic-targeted', side: 'right', defaultSideWidth: 380 }, {
      default: () => [
        h('section', { class: 'pane primary-pane traffic-main-pane' }, [
          sectionTitle({
            title: isRandom.value ? '随机引流批次' : '定向引流批次',
            subtitle: isRandom.value ? '打开抖音推荐流后按设置自动执行' : '复用已采集视频或搜索关键词生成执行队列',
            icon: Promotion,
            tone: isRandom.value ? 'purple' : 'teal',
            aside: h('button', { class: 'secondary-action', disabled: loading.value, onClick: loadAll }, [h(Refresh, { class: 'inline-icon' }), '刷新']),
          }),
          sectionTitle({ title: '创建计划', subtitle: '动作、文案、限额和停留时间统一在引流设置中配置', icon: Promotion, tone: 'teal', compact: true }),
          renderCampaignForm(form, isRandom.value, assets.value, keywordOptions.value),
          h('div', { class: 'action-row' }, [
            h('button', { class: 'primary-action', onClick: createCampaign }, [h(Promotion, { class: 'inline-icon' }), '创建计划']),
            showBuildTargets.value ? h('button', { class: 'secondary-action', disabled: !selectedCampaignId.value, onClick: buildTargets }, '生成队列') : null,
            h('button', { class: 'primary-action', disabled: !selectedCampaignId.value, onClick: startRun }, [h(CaretRight, { class: 'inline-icon' }), '开始执行']),
          ]),
          renderTargets(targets.value, loadTargets),
        ]),
      ],
      side: () => [
        h('aside', { class: 'pane side-pane' }, [
          sectionTitle({ title: '计划列表', subtitle: '选择一个计划查看队列', icon: DataLine, tone: 'blue' }),
          campaigns.value.length
            ? h('div', { class: 'traffic-campaign-list' }, campaigns.value.map((item: Dict) => h('button', {
                class: ['traffic-campaign-item', Number(item.id) === selectedCampaignId.value ? 'active' : ''],
                onClick: async () => { selectedCampaignId.value = Number(item.id); await loadTargets() }
              }, [
                h('strong', item.name),
                h('span', `${sourceTypeLabel(item.source_type)} · 待执行 ${item.pending_count || 0} · 已完成 ${item.succeeded_count || 0}`),
              ])))
            : emptyState({ title: '还没有计划', description: '先创建一个引流计划。', icon: Promotion, tone: 'gray' }),
          renderRunPanel(latestRun.value, cancelRun),
        ]),
      ],
    })
  }
})

function renderCampaignForm(form: Dict, isRandom: boolean, assets: Dict[], keywordOptions: Dict[]) {
  return h('div', { class: 'traffic-form' }, [
    field('计划名称', h('input', { value: form.name, placeholder: isRandom ? '随机引流计划' : '竞品视频引流计划', onInput: (event: Event) => form.name = (event.target as HTMLInputElement).value })),
    !isRandom ? field('来源', h('select', { value: form.source_type, onChange: (event: Event) => form.source_type = (event.target as HTMLSelectElement).value }, [
      h('option', { value: 'competitor' }, '竞品账号视频'),
      h('option', { value: 'keyword' }, '已采集关键词视频'),
      h('option', { value: 'search_keyword' }, '搜索关键词引流'),
    ])) : null,
    !isRandom && form.source_type === 'keyword' ? field('关键词', renderKeywordPicker(form, keywordOptions)) : null,
    !isRandom && form.source_type === 'search_keyword' ? field('搜索关键词', h('input', { value: form.keyword, placeholder: '输入要在抖音搜索的视频关键词', onInput: (event: Event) => form.keyword = (event.target as HTMLInputElement).value })) : null,
    field('发送内容', h('div', { class: 'traffic-checks' }, [
      check(form, 'action_comment', '发送文案'),
      check(form, 'action_image', '发送图片'),
    ])),
    form.action_image
      ? field('图片素材', assets.length
          ? h('div', { class: 'traffic-asset-checks' }, assets.map(asset => assetCheck(form, asset)))
          : h('small', '请先到“引流设置”上传图片素材'))
      : null,
  ])
}

function renderKeywordPicker(form: Dict, keywordOptions: Dict[]) {
  return h('div', { class: 'traffic-keyword-picker' }, [
    h('input', { value: form.keyword, readonly: true, placeholder: '从下方关键词中选择' }),
    keywordOptions.length
      ? h('div', { class: 'traffic-keyword-list' }, keywordOptions.map(row => h('button', {
          type: 'button',
          class: ['traffic-keyword-chip', form.keyword === row.keyword ? 'active' : ''],
          onClick: () => form.keyword = row.keyword,
        }, [
          h('strong', row.keyword),
          h('span', `${row.target_count || 0} 条视频`),
        ])))
      : h('small', '暂无已入库的抖音关键词视频，请先通过拓客工作台采集关键词内容。'),
  ])
}

function renderRunPanel(run: Dict | null, cancelRun: (run: Dict) => void) {
  return h('div', { class: 'traffic-side-block' }, [
    sectionTitle({ title: '最近批次', subtitle: run ? `${run.campaign_name || ''} · ${run.status}` : '暂无运行记录', icon: CaretRight, tone: 'green', compact: true }),
    run
      ? h('div', { class: 'traffic-run-card' }, [
          h('div', [h('strong', run.id), h('span', `成功 ${run.counts?.succeeded || 0} / 失败 ${run.counts?.failed || 0} / 跳过 ${run.counts?.skipped || 0}`)]),
          run.status === 'running' ? h('button', { class: 'secondary-action danger-action', onClick: () => cancelRun(run) }, [h(Close, { class: 'inline-icon' }), '停止']) : null,
          h('div', { class: 'traffic-events' }, (run.events || []).slice(0, 8).map((event: Dict) => h('p', [h('b', event.status), ` ${event.action} ${event.detail || ''}`]))),
        ])
      : emptyState({ title: '暂无批次', description: '创建计划后点击开始执行。', icon: CaretRight, tone: 'gray' })
  ])
}

function renderTargets(targets: Dict, loadTargets: (page?: number) => Promise<void>) {
  const rows = targets.rows || []
  const page = Number(targets.page || 1)
  const pageSize = Number(targets.page_size || 30)
  const total = Number(targets.total || 0)
  const pageEnd = Math.min(page * pageSize, total)
  const hasNext = pageEnd < total
  return h('div', { class: 'table-content traffic-target-content' }, [
    sectionTitle({ title: '执行队列', subtitle: `共 ${targets.total || 0} 条`, icon: VideoCamera, tone: 'purple', compact: true }),
    h('div', { class: 'table-scroll' }, [
      h('table', { class: 'data-table traffic-target-data-table' }, [
        h('thead', [h('tr', [
          h('th', '视频'),
          h('th', '作者/来源'),
          h('th', '发送内容'),
          h('th', '状态'),
        ])]),
        h('tbody', rows.length ? rows.map((row: Dict) => h('tr', [
          h('td', [renderTargetTitle(row)]),
          h('td', [h('span', { class: 'table-muted-text' }, row.author_name || row.keyword || row.source_type || '-')]),
          h('td', [h('span', { class: 'table-muted-text' }, row.selected_comment || '执行时随机选择文案/图片')]),
          h('td', [h('span', { class: ['traffic-status-pill', `status-${row.status || 'pending'}`] }, row.status || 'pending')]),
        ])) : [
          h('tr', [h('td', { class: 'table-empty', colspan: 4 }, '队列为空；已采集定向引流先生成队列，搜索关键词和随机引流会在运行时写入队列。')]),
        ]),
      ]),
    ]),
    h('div', { class: 'table-pagination' }, [
      h('span', total ? `显示 ${(page - 1) * pageSize + 1}-${pageEnd} / ${total}` : '0 条记录'),
      h('div', { class: 'table-page-controls' }, [
        h('button', { type: 'button', disabled: page <= 1, onClick: () => loadTargets(page - 1) }, '上一页'),
        h('span', `${page} / ${Math.max(1, Math.ceil(total / pageSize))}`),
        h('button', { type: 'button', disabled: !hasNext, onClick: () => loadTargets(page + 1) }, '下一页'),
      ]),
    ]),
  ])
}

function renderTargetTitle(row: Dict) {
  const label = row.title || row.content_url || `视频 ${row.id}`
  const attrs = { class: 'table-primary-text', title: label }
  return row.content_url
    ? h('a', { ...attrs, class: 'table-primary-link table-primary-text', href: row.content_url, target: '_blank', rel: 'noreferrer' }, label)
    : h('span', attrs, label)
}

function sourceTypeLabel(sourceType: string) {
  if (sourceType === 'keyword') return '已采集关键词视频'
  if (sourceType === 'search_keyword') return '搜索关键词引流'
  if (sourceType === 'random_feed') return '随机推荐流'
  return '竞品账号视频'
}

function field(label: string, control: any) {
  return h('label', { class: 'traffic-field' }, [h('span', label), control])
}

function check(form: Dict, key: string, label: string) {
  return h('label', { class: 'traffic-check' }, [
    h('input', { type: 'checkbox', checked: Boolean(form[key]), onChange: (event: Event) => form[key] = (event.target as HTMLInputElement).checked }),
    label,
  ])
}

function assetCheck(form: Dict, asset: Dict) {
  const assetId = Number(asset.id)
  const selected = selectedAssetIds(form.image_asset_ids)
  return h('label', { class: 'traffic-check' }, [
    h('input', {
      type: 'checkbox',
      checked: selected.includes(assetId),
      onChange: (event: Event) => {
        const current = selectedAssetIds(form.image_asset_ids)
        form.image_asset_ids = (event.target as HTMLInputElement).checked
          ? Array.from(new Set([...current, assetId]))
          : current.filter(id => id !== assetId)
      },
    }),
    asset.name || asset.file_name || `图片 ${asset.id}`,
  ])
}

function selectedAssetIds(value: unknown) {
  return Array.isArray(value) ? value.map(item => Number(item)).filter(Number.isFinite) : []
}
