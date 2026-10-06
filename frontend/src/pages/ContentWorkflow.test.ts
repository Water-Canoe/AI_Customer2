import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ContentWorkbenchPage from './ContentWorkbenchPage'
import PublishCenterPage from './PublishCenterPage'
import { ListPagination } from '../components/ui/ListPagination'

const mocks = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), push: vi.fn(), replace: vi.fn(), warning: vi.fn(), error: vi.fn(), confirm: vi.fn(), route: {} as Record<string, any>, updateGuard: undefined as undefined | (() => Promise<boolean>) }))
vi.mock('../shared/api', () => ({ api: mocks }))
vi.mock('vue-router', () => ({ useRoute: () => mocks.route, useRouter: () => ({ push: mocks.push, replace: mocks.replace }), onBeforeRouteLeave: vi.fn(), onBeforeRouteUpdate: (guard: () => Promise<boolean>) => { mocks.updateGuard = guard } }))
vi.mock('element-plus', () => ({ ElMessage: { success: vi.fn(), warning: mocks.warning, error: mocks.error }, ElMessageBox: { confirm: mocks.confirm }, ElPagination: { render: () => null } }))

const page = (items: any[] = [], params: any = {}) => ({ items, total: items.length, active: 0, summary: { active: 0 }, page: params.page || 1, page_size: params.page_size || 20 })
const accounts = ['dy', 'xhs'].map(platform => ({ id: platform, platform, name: platform + '账号', enabled: true, features: ['publish'], default_features: ['publish'], feature_status: { publish: { status: 'ready' } } }))
const outputs = ['old-output', 'new-output'].map((id, index) => ({ id, output_id: id, video_job_id: 'job', attempt: index + 1, index: 0, name: 'final.mp4', subject: '作品主题', script: '作品正文', url: '/outputs/' + id, publish_tasks: [] }))
const videoPayload = () => ({ subject: '原始作品', params: { video_subject: '原始作品', video_script: '原始文案', video_source: 'local', voice_name: 'zh-CN-XiaoxiaoNeural-Female', video_count: 2, voice_volume: 0.7 }, asset_ids: ['asset-0'], audio_asset_id: '', bgm_asset_id: '', voice_provider: 'edge', voice_mode: 'tts', publish_plan: { enabled: true, account_ids: [], output_scope: 'all', publish_strategy: 'immediate', scheduled_at: '' } })
const publishPayload = () => ({ source: { type: 'video_output', output_ids: outputs.map(output => output.id) }, title: '统一标题', description: '统一正文', tags: '#原始', account_ids: ['dy', 'xhs'], account_overrides: { xhs: { title: '小红书独立标题', description: '独立正文', tags: '独立', publish_strategy: 'immediate', scheduled_at: '', options: {} } }, publish_strategy: 'immediate', scheduled_at: '' })

describe('内容创作到发布', () => {
  let wrapper: VueWrapper | undefined
  let drafts: Record<string, any>
  let tasks: any[]
  let contentSettings: any

  // 所有供应商、文件和发布操作使用内存响应，测试不会提交真实任务。
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-06T00:00:00Z'))
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    mocks.route = reactive({ name: 'content-create', fullPath: '/content-create?draft_id=work-1', query: { draft_id: 'work-1' } })
    drafts = { '/drafts/video/work-1': videoPayload(), '/drafts/publish/publish-1': publishPayload() }
    tasks = []
    contentSettings = { model_connection_mode: 'shared', model_connection: { mode: 'shared', base_url: 'https://model.example/v1', model: 'shared-model', api_key_configured: true }, app: { video_source: 'local', llm_provider: 'openai', openai_api_key: '', openai_api_key_configured: true, openai_base_url: 'https://custom.example/v1', openai_model_name: 'custom-model', gemini_api_key: '', gemini_api_key_configured: true, pexels_api_keys: [], pexels_api_keys_configured: true } }
    mocks.get.mockImplementation(async (url: string, options?: any) => {
      if (url.startsWith('/drafts/') && url.split('/').length === 4) return { data: { exists: Boolean(drafts[url]), payload: drafts[url] || {}, updated_at: '2026-10-06' } }
      if (url === '/drafts/video' || url === '/drafts/publish') return { data: page([], options?.params) }
      if (url === '/accounts') return { data: accounts }
      if (url === '/content/assets') return { data: Array.from({ length: 14 }, (_, index) => ({ id: 'asset-' + index, name: '素材' + index, asset_type: 'video' })) }
      if (url === '/content/voice-profiles') return { data: [] }
      if (url === '/content/voices') return { data: ['zh-CN-XiaoxiaoNeural-Female'] }
      if (url === '/content/video-jobs') return { data: page([], options?.params) }
      if (url === '/content/outputs') return { data: { ...page(outputs, options?.params), total: 30 } }
      if (url.startsWith('/content/outputs/')) return { data: outputs.find(output => url.endsWith(output.id)) }
      if (url === '/content/publish-tasks') return { data: page(tasks, options?.params) }
      if (url === '/content/settings') return { data: contentSettings }
      if (url === '/content/environment-check') return { data: { ok: true } }
      throw new Error('未定义的只读请求：' + url)
    })
    mocks.put.mockImplementation(async (url: string, body: any) => {
      if (url === '/content/settings') return { data: contentSettings }
      drafts[url] = JSON.parse(JSON.stringify(body.payload)); return { data: { updated_at: 'saved' } }
    })
    mocks.delete.mockImplementation(async (url: string) => { delete drafts[url]; return { data: {} } })
    mocks.post.mockImplementation(async (url: string, body: any) => {
      if (url === '/content/video-jobs') {
        drafts['/drafts/video/' + body.draft_key].return_context = { route: '/content-records', query: { job: 'created-job' }, selection: [] }
        return { data: { id: 'created-job', subject: '生成任务', status: 'queued', current_stage: 'queued' } }
      }
      if (url === '/content/publish-tasks') {
        const created = ['task-1', 'task-2', 'task-3', 'task-4'].map(id => ({ id }))
        drafts['/drafts/publish/' + body.draft_key].return_context = { route: '/content-publish', query: { task: created[0]!.id }, selection: created.map(task => task.id) }
        return { data: created }
      }
      return { data: {} }
    })
    mocks.patch.mockResolvedValue({ data: {} })
    mocks.push.mockResolvedValue(undefined)
    mocks.replace.mockImplementation(async (location: any) => {
      const path = typeof location === 'string' ? location : location.path
      mocks.route.query = typeof location === 'string' ? {} : location.query || {}
      mocks.route.name = path.replace(/^\//, '')
      const query = new URLSearchParams(mocks.route.query).toString()
      mocks.route.fullPath = path + (query ? '?' + query : '')
    })
    mocks.confirm.mockResolvedValue(undefined)
  })
  afterEach(async () => { wrapper?.unmount(); wrapper = undefined; await flushPromises(); vi.clearAllMocks(); vi.useRealTimers() })

  const button = (text: string) => wrapper!.findAll('button').find(item => item.text() === text)!
  const input = (label: string, tag = 'input') => wrapper!.findAll('label.form-field').find(item => item.get('span').text() === label)!.get(tag)

  it('恢复视频草稿，保留未完成的配音选择和用户清空的发布账号，刷新不覆盖修改', async () => {
    wrapper = mount(ContentWorkbenchPage)
    await flushPromises()
    expect((input('作品主题').element as HTMLInputElement).value).toBe('原始作品')
    expect(wrapper.findAll('.content-auto-publish input[type="checkbox"]').slice(1).every(item => !(item.element as HTMLInputElement).checked)).toBe(true)
    await input('作品主题').setValue('人工编辑作品')
    await input('配音方式', 'select').setValue('file')
    await wrapper.setProps({ refreshSeq: 1 })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(500)
    expect((input('作品主题').element as HTMLInputElement).value).toBe('人工编辑作品')
    expect(drafts['/drafts/video/work-1']).toMatchObject({ subject: '人工编辑作品', voice_mode: 'file', audio_asset_id: '', asset_ids: ['asset-0'], params: { voice_volume: 0.7 }, publish_plan: { enabled: true, account_ids: [] } })
    expect(mocks.get.mock.calls.filter(([url]) => url === '/drafts/video/work-1')).toHaveLength(1)
    await button('生成视频').trigger('click')
    expect(mocks.warning).toHaveBeenCalledWith('请先导入并选择配音文件')
    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('在创作页同页选择素材，搜索和刷新后原选择仍进入持久草稿', async () => {
    drafts['/drafts/video/work-1'].publish_plan.enabled = false
    wrapper = mount(ContentWorkbenchPage)
    await flushPromises()
    await button('选择或导入素材').trigger('click')
    await wrapper.get('.content-picker input[placeholder="搜索素材名称"]').setValue('素材13')
    await wrapper.get('.content-picker-card').trigger('click')
    await wrapper.get('.content-picker input[placeholder="搜索素材名称"]').setValue('没有匹配')
    expect(wrapper.get('.content-picker .workspace-note').text()).toContain('已选 2 项')
    await button('完成选择').trigger('click')
    await wrapper.setProps({ refreshSeq: 1 })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(500)
    expect(drafts['/drafts/video/work-1'].asset_ids).toEqual(['asset-0', 'asset-13'])
    expect(mocks.get.mock.calls.filter(([url]) => url === '/content/assets').every(call => call.length === 1)).toBe(true)
  })

  it('素材库搜索保留图文选择，显式删除所选素材后只移除其发布引用', async () => {
    let images = ['image-1', 'image-2'].map(id => ({ id, name: id, asset_type: 'image', preview_url: '/' + id }))
    const originalGet = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options?: any) => url === '/content/assets' ? Promise.resolve({ data: images }) : originalGet(url, options))
    mocks.delete.mockImplementation(async (url: string) => { images = images.filter(image => !url.endsWith(image.id)); return { data: {} } })
    mocks.route.name = 'content-assets'; mocks.route.query = {}; mocks.route.fullPath = '/content-assets'
    wrapper = mount(ContentWorkbenchPage)
    await flushPromises()
    for (const checkbox of wrapper.findAll('.content-asset-publish-select input')) await checkbox.setValue(true)
    await wrapper.get('input[placeholder="搜索资产名称"]').setValue('image-1')
    await button('搜索').trigger('click')
    await flushPromises()
    expect(button('发布图文（2）').exists()).toBe(true)
    await button('删除').trigger('click')
    await flushPromises()
    await button('发布图文（1）').trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({ path: '/content-publish', query: { asset_ids: 'image-2' } })
  })

  it('已成功建立生成任务时，草稿清理失败不会报告成创建失败', async () => {
    drafts['/drafts/video/work-1'].publish_plan.enabled = false
    mocks.delete.mockRejectedValueOnce({ response: { data: { detail: '草稿删除失败' } } })
    wrapper = mount(ContentWorkbenchPage)
    await flushPromises()
    await button('生成视频').trigger('click')
    await flushPromises()
    expect(mocks.post).toHaveBeenCalledWith('/content/video-jobs', expect.objectContaining({ draft_key: 'work-1', asset_ids: ['asset-0'], params: expect.objectContaining({ voice_volume: 0.7 }) }))
    expect(mocks.error).toHaveBeenCalledWith(expect.stringContaining('视频任务已创建'))
    expect(mocks.error).toHaveBeenCalledWith(expect.stringContaining('请勿重复提交'))
    expect(wrapper.findAll('button').some(item => item.text() === '生成视频')).toBe(false)
    wrapper.unmount()
    await flushPromises()
    wrapper = mount(ContentWorkbenchPage)
    await flushPromises()
    expect(wrapper.text()).toContain('该草稿已提交')
    expect(wrapper.findAll('button').some(item => item.text() === '生成视频')).toBe(false)
    await button('查看已创建生成任务').trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({ path: '/content-records', query: { job: 'created-job' } })
    await button('重试清理已提交草稿').trigger('click')
    await flushPromises()
    expect(mocks.post).toHaveBeenCalledTimes(1)
  })

  it('作品库查询服务端全部成品，搜索重置页码并按稳定 ID 跨尝试准备发布', async () => {
    mocks.route.name = 'content-records'; mocks.route.query = {}; mocks.route.fullPath = '/content-records'
    wrapper = mount(ContentWorkbenchPage)
    await flushPromises()
    wrapper.getComponent(ListPagination).vm.$emit('change', { page: 2, page_size: 12 })
    await flushPromises()
    expect(mocks.get).toHaveBeenLastCalledWith('/content/outputs', { params: { page: 2, page_size: 12, search: '' } })
    await wrapper.get('input[placeholder="搜索全部作品主题或文件名"]').setValue('旧成品')
    await button('搜索').trigger('click')
    await flushPromises()
    expect(mocks.get).toHaveBeenLastCalledWith('/content/outputs', { params: { page: 1, page_size: 12, search: '旧成品' } })
    for (const checkbox of wrapper.findAll('.content-output-select input')) await checkbox.setValue(true)
    await button('发布所选 2 个成品').trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({ path: '/content-publish', query: { output_ids: 'old-output,new-output' } })
    await button('生成记录').trigger('click')
    await flushPromises()
    expect(mocks.get).toHaveBeenLastCalledWith('/content/video-jobs', expect.objectContaining({ params: expect.objectContaining({ search: '旧成品' }) }))
    await button('草稿').trigger('click')
    await flushPromises()
    expect(mocks.get).toHaveBeenLastCalledWith('/drafts/video', expect.objectContaining({ params: expect.objectContaining({ search: '旧成品' }) }))
  })

  it('作品库在复用组件时跟随外部 URL 页签，页签操作同步 URL，job 深链优先', async () => {
    const originalGet = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options?: any) => url === '/content/video-jobs/exact-job' ? Promise.resolve({ data: { id: 'exact-job', subject: '精确生成任务', status: 'failed', outputs: [], params: {}, assets: [] } }) : originalGet(url, options))
    mocks.route.name = 'content-records'; mocks.route.query = { tab: 'jobs' }; mocks.route.fullPath = '/content-records?tab=jobs'
    wrapper = mount(ContentWorkbenchPage)
    await flushPromises()
    expect(wrapper.get('.workspace-tabs .is-active').text()).toBe('生成记录')
    mocks.route.query = { tab: 'outputs' }; mocks.route.fullPath = '/content-records?tab=outputs'
    await flushPromises()
    expect(wrapper.get('.workspace-tabs .is-active').text()).toBe('成品')
    await button('草稿').trigger('click')
    await flushPromises()
    expect(mocks.replace).toHaveBeenLastCalledWith({ path: '/content-records', query: { tab: 'drafts' } })
    expect(wrapper.get('.workspace-tabs .is-active').text()).toBe('草稿')
    mocks.route.query = { tab: 'outputs', job: 'exact-job' }; mocks.route.fullPath = '/content-records?tab=outputs&job=exact-job'
    await flushPromises()
    expect(wrapper.get('.workspace-tabs .is-active').text()).toBe('生成记录')
    expect(mocks.get).toHaveBeenCalledWith('/content/video-jobs/exact-job')
    await button('成品').trigger('click')
    await flushPromises()
    expect(mocks.replace).toHaveBeenLastCalledWith({ path: '/content-records', query: { tab: 'outputs' } })
    expect(wrapper.get('.workspace-tabs .is-active').text()).toBe('成品')
  })

  it('发布草稿恢复账号文案矩阵，超限保持原文，调整后提交多个稳定成品和独立账号文案', async () => {
    mocks.route.name = 'content-publish'; mocks.route.query = { draft_id: 'publish-1' }; mocks.route.fullPath = '/content-publish?draft_id=publish-1'
    wrapper = mount(PublishCenterPage)
    await flushPromises()
    expect(wrapper.get('.publish-submit-summary strong').text()).toContain('2 份内容 × 2 个账号 = 4 条发布任务')
    const title = wrapper.get('.publish-composer .publish-field input')
    await title.setValue('长'.repeat(21))
    await button('确认创建发布任务').trigger('click')
    expect(mocks.warning).toHaveBeenCalledWith(expect.stringContaining('标题最多 20 字'))
    expect((title.element as HTMLInputElement).value).toBe('长'.repeat(21))
    expect(mocks.post).not.toHaveBeenCalled()
    await title.setValue('调整后的统一标题')
    await wrapper.findAll('.publish-account-editor')[1]!.get('input').setValue('调整后小红书标题')
    await wrapper.setProps({ refreshSeq: 1 })
    await flushPromises()
    expect((title.element as HTMLInputElement).value).toBe('调整后的统一标题')
    await vi.advanceTimersByTimeAsync(500)
    expect(drafts['/drafts/publish/publish-1'].account_overrides.xhs.title).toBe('调整后小红书标题')
    await button('确认创建发布任务').trigger('click')
    await flushPromises()
    expect(mocks.post).toHaveBeenCalledWith('/content/publish-tasks', expect.objectContaining({ source: { type: 'video_output', output_ids: ['old-output', 'new-output'] }, account_ids: ['dy', 'xhs'], account_overrides: { dy: expect.objectContaining({ title: '调整后的统一标题', tags: ['原始'] }), xhs: expect.objectContaining({ title: '调整后小红书标题', description: '独立正文', tags: ['独立'] }) } }))
    expect(mocks.delete).toHaveBeenCalledWith('/drafts/publish/publish-1')
  })

  it('生成失败的发布任务返回生成记录；结果不明确的重试必须先人工确认', async () => {
    tasks = [{ id: 'waiting-failed', status: 'failed', source_type: 'video_output', output_id: '', video_job_id: 'failed-job', platform: 'dy', account_name: 'dy账号', title: '未生成' }, { id: 'review', status: 'review_required', source_type: 'video_output', output_id: 'old-output', platform: 'dy', account_name: 'dy账号', title: '待核对' }]
    mocks.route.name = 'content-publish'; mocks.route.query = {}; mocks.route.fullPath = '/content-publish'
    wrapper = mount(PublishCenterPage)
    await flushPromises()
    const failed = wrapper.findAll('.publish-task-card')[0]!
    expect(failed.text()).toContain('生成未完成，请先恢复视频生成')
    expect(failed.text()).not.toContain('重试')
    await failed.findAll('button').find(item => item.text() === '查看生成')!.trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({ path: '/content-records', query: { job: 'failed-job' } })
    mocks.confirm.mockRejectedValueOnce('cancel')
    await button('确认未发布后重试').trigger('click')
    await flushPromises()
    expect(mocks.post).not.toHaveBeenCalled()
    await button('确认未发布后重试').trigger('click')
    await flushPromises()
    expect(mocks.patch).toHaveBeenCalledWith('/content/publish-tasks/review/mark-result', { status: 'failed', note: '人工在平台核对未发布，允许重试' })
    expect(mocks.post).toHaveBeenCalledWith('/content/publish-tasks/review/retry')
  })

  it('发布矩阵创建后清理失败仍锁定重提，重新打开草稿无需原素材也能查看结果和只重试清理', async () => {
    mocks.route.name = 'content-publish'; mocks.route.query = { draft_id: 'publish-1' }; mocks.route.fullPath = '/content-publish?draft_id=publish-1'
    mocks.delete.mockRejectedValueOnce(new Error('清理中断'))
    wrapper = mount(PublishCenterPage)
    await flushPromises()
    await button('确认创建发布任务').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('button').some(item => item.text() === '确认创建发布任务')).toBe(false)
    expect(mocks.post).toHaveBeenCalledWith('/content/publish-tasks', expect.objectContaining({ draft_key: 'publish-1' }))
    wrapper.unmount()
    await flushPromises()
    const originalGet = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options?: any) => url.startsWith('/content/outputs/') ? Promise.reject(new Error('原素材已失效')) : originalGet(url, options))
    wrapper = mount(PublishCenterPage)
    await flushPromises()
    expect(wrapper.text()).toContain('此草稿已创建发布任务')
    expect(wrapper.findAll('button').some(item => item.text() === '确认创建发布任务')).toBe(false)
    expect(drafts['/drafts/publish/publish-1'].return_context.selection).toEqual(['task-1', 'task-2', 'task-3', 'task-4'])
    await button('查看已创建发布任务').trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({ path: '/content-publish', query: { task: 'task-1' } })
    await button('重试清理已提交草稿').trigger('click')
    await flushPromises()
    expect(mocks.post).toHaveBeenCalledTimes(1)
    expect(mocks.delete.mock.calls.filter(([url]) => url === '/drafts/publish/publish-1')).toHaveLength(2)
  })

  it('任务中心跳转按 job 精确读取列表外的生成记录和旧成品，并保留原供应商建立新稿', async () => {
    const job = { id: 'outside-page', subject: '失败后仍保留旧成品', status: 'failed', attempt: 2, error: '本次生成失败', params: { video_subject: '失败后仍保留旧成品', voice_name: 'zh-CN-XiaoxiaoMultilingualNeural-V2-Female', video_source: 'local' }, assets: [{ id: 'asset-0', role: 'material' }], outputs: [outputs[0]] }
    const originalGet = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options?: any) => url === '/content/video-jobs/outside-page' ? Promise.resolve({ data: job }) : originalGet(url, options))
    mocks.route.name = 'content-records'; mocks.route.query = { job: 'outside-page' }; mocks.route.fullPath = '/content-records?job=outside-page'
    wrapper = mount(ContentWorkbenchPage)
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledWith('/content/video-jobs/outside-page')
    expect(wrapper.get('.content-job-detail').text()).toContain('本次生成失败')
    expect(wrapper.get('.content-job-detail video').attributes('src')).toBe('/outputs/old-output')
    await wrapper.setProps({ refreshSeq: 1 })
    await flushPromises()
    expect(mocks.get.mock.calls.filter(([url]) => url === '/content/video-jobs/outside-page')).toHaveLength(2)
    await button('修改后重新生成').trigger('click')
    await flushPromises()
    expect(mocks.put).toHaveBeenCalledWith(expect.stringMatching(/^\/drafts\/video\//), { payload: expect.objectContaining({ asset_ids: ['asset-0'], voice_provider: 'azure-v2', voice_mode: 'tts', publish_plan: expect.objectContaining({ enabled: false }) }) })
  })

  it('多次生成按成品自身尝试与时间显示，任务入口要求选择成品，单个发布使用所选稳定 ID', async () => {
    const ownOutputs = outputs.map((output, index) => ({ ...output, created_at: '2026-10-0' + (index + 1) }))
    const job = { id: 'multi-attempt', subject: '多次生成作品', status: 'succeeded', current_stage: 'completed', attempt: 2, finished_at: '2026-10-05', params: {}, assets: [], outputs: ownOutputs }
    const originalGet = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options?: any) => url === '/content/video-jobs' ? Promise.resolve({ data: page([job], options?.params) }) : url === '/content/video-jobs/multi-attempt' ? Promise.resolve({ data: job }) : originalGet(url, options))
    mocks.route.name = 'content-records'; mocks.route.query = { job: 'multi-attempt' }; mocks.route.fullPath = '/content-records?job=multi-attempt'
    wrapper = mount(ContentWorkbenchPage)
    await flushPromises()
    await button('选择成品发布').trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({ path: '/content-records', query: { job: 'multi-attempt' } })
    expect(mocks.push.mock.calls.some(([location]) => location.path === '/content-publish')).toBe(false)
    const cards = wrapper.findAll('.content-job-detail .content-output-card')
    expect(cards[0]!.get('strong').text()).toBe('作品主题')
    expect(cards[0]!.text()).toContain('第1次生成')
    expect(cards[0]!.get('time').text()).toContain('2026-10-01')
    expect(cards[1]!.text()).toContain('第2次生成')
    expect(cards[1]!.get('time').text()).toContain('2026-10-02')
    await cards[1]!.findAll('button').find(item => item.text() === '准备发布')!.trigger('click')
    expect(mocks.push).toHaveBeenLastCalledWith({ path: '/content-publish', query: { output_ids: 'new-output' } })
  })

  it('任务中心按 task 精确读取列表外发布详情，刷新不依赖当前分页', async () => {
    const detail = { id: 'outside-page', status: 'succeeded', current_stage: 'completed', source_type: 'video_output', output_id: 'old-output', output_name: '旧成品.mp4', video_job_id: 'job', title: '旧成品标题', description: '已确认的发布正文', tags: ['原始标签'], account_name: 'dy账号', platform: 'dy' }
    const originalGet = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options?: any) => url === '/content/publish-tasks/outside-page' ? Promise.resolve({ data: detail }) : originalGet(url, options))
    mocks.route.name = 'content-publish'; mocks.route.query = { task: 'outside-page' }; mocks.route.fullPath = '/content-publish?task=outside-page'
    wrapper = mount(PublishCenterPage)
    await flushPromises()
    expect(wrapper.get('.publish-task-detail').text()).toContain('已确认的发布正文')
    expect(wrapper.get('.publish-task-detail').text()).toContain('旧成品.mp4')
    expect(wrapper.get('.publish-task-detail').text()).toContain('#原始标签')
    await wrapper.setProps({ refreshSeq: 1 })
    await flushPromises()
    expect(mocks.get.mock.calls.filter(([url]) => url === '/content/publish-tasks/outside-page')).toHaveLength(2)
    await button('查看对应生成记录').trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({ path: '/content-records', query: { job: 'job' } })
  })

  it('视频草稿首次读取失败后输入和选择仍在，成功重试不会被服务器旧稿覆盖', async () => {
    const originalGet = mocks.get.getMockImplementation()!
    let fail = true
    mocks.get.mockImplementation((url: string, options?: any) => {
      if (url === '/drafts/video/work-1' && fail) { fail = false; return Promise.reject(new Error('连接中断')) }
      return originalGet(url, options)
    })
    wrapper = mount(ContentWorkbenchPage)
    await flushPromises()
    await input('作品主题').setValue('断网后继续输入')
    await input('配音方式', 'select').setValue('file')
    expect(mocks.put).not.toHaveBeenCalled()
    await button('重试读取草稿').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(500)
    expect((input('作品主题').element as HTMLInputElement).value).toBe('断网后继续输入')
    expect(drafts['/drafts/video/work-1']).toMatchObject({ params: { video_subject: '断网后继续输入' }, voice_mode: 'file', audio_asset_id: '', asset_ids: [] })
  })

  it('草稿与默认配置读取完整结束前禁止新建、清空和同组件切换，结束后切换先保存输入', async () => {
    delete drafts['/drafts/video/work-1']
    const originalGet = mocks.get.getMockImplementation()!
    let finishDraft!: (value: any) => void
    let finishSettings!: (value: any) => void
    mocks.get.mockImplementation((url: string, options?: any) => url === '/drafts/video/work-1' ? new Promise(resolve => { finishDraft = resolve }) : url === '/content/settings' ? new Promise(resolve => { finishSettings = resolve }) : originalGet(url, options))
    wrapper = mount(ContentWorkbenchPage)
    await flushPromises()
    expect(button('新建草稿').element).toHaveProperty('disabled', true)
    expect(button('清空草稿').element).toHaveProperty('disabled', true)
    expect(await mocks.updateGuard!()).toBe(false)
    await button('新建草稿').trigger('click')
    expect(mocks.push).not.toHaveBeenCalled()
    finishDraft({ data: { exists: false, payload: {}, updated_at: null } })
    await flushPromises()
    expect(button('新建草稿').element).toHaveProperty('disabled', true)
    expect(button('生成视频').element).toHaveProperty('disabled', true)
    expect(await mocks.updateGuard!()).toBe(false)
    finishSettings({ data: contentSettings })
    await flushPromises()
    expect(button('新建草稿').element).toHaveProperty('disabled', false)
    await input('作品主题').setValue('切换前必须保存')
    expect(await mocks.updateGuard!()).toBe(true)
    expect(drafts['/drafts/video/work-1'].params.video_subject).toBe('切换前必须保存')
  })

  it('发布草稿写入失败显示明确重试，并保存已有人工文案而不重新读取旧稿', async () => {
    mocks.route.name = 'content-publish'; mocks.route.query = { draft_id: 'publish-1' }; mocks.route.fullPath = '/content-publish?draft_id=publish-1'
    mocks.put.mockRejectedValueOnce(new Error('连接中断'))
    wrapper = mount(PublishCenterPage)
    await flushPromises()
    const title = wrapper.get('.publish-composer .publish-field input')
    await title.setValue('断网编辑标题')
    await vi.advanceTimersByTimeAsync(500)
    expect(wrapper.get('.publish-submit-summary').text()).toContain('草稿保存失败')
    await button('重试保存草稿').trigger('click')
    await flushPromises()
    expect(drafts['/drafts/publish/publish-1'].title).toBe('断网编辑标题')
    expect((title.element as HTMLInputElement).value).toBe('断网编辑标题')
    expect(mocks.get.mock.calls.filter(([url]) => url === '/drafts/publish/publish-1')).toHaveLength(1)
  })

  it('内容模型默认复用全局，单独配置只展示所选供应商；空密钥保留、显式清除单独提交', async () => {
    mocks.route.name = 'content-settings'; mocks.route.query = {}; mocks.route.fullPath = '/content-settings'
    wrapper = mount(ContentWorkbenchPage)
    await flushPromises()
    expect(wrapper.get('.content-model-connection').text()).toContain('shared-model')
    expect(wrapper.findAll('details').some(item => item.get('summary').text().includes('AI供应商'))).toBe(false)
    expect(input('Gemini 文案与配音 API Key').element).toHaveProperty('value', '')
    await input('模型连接方式', 'select').setValue('custom')
    expect(input('OpenAI模型').element).toHaveProperty('value', 'custom-model')
    expect(wrapper.findAll('label.form-field').some(item => item.get('span').text() === 'Moonshot API Key')).toBe(false)
    const credential = input('OpenAI API Key').element.parentElement!.parentElement!
    expect(credential.textContent).toContain('已配置；留空保留')
    await button('保存设置').trigger('click')
    await flushPromises()
    expect(mocks.put).toHaveBeenCalledWith('/content/settings', { values: expect.objectContaining({ model_connection_mode: 'custom', app: expect.objectContaining({ openai_api_key: '', pexels_api_keys: [] }) }) })
    const saved = mocks.put.mock.calls[0]![1]
    expect(saved.values).not.toHaveProperty('model_connection')
    expect(saved.values.app).not.toHaveProperty('openai_api_key_configured')
    expect(saved).not.toHaveProperty('clear_secrets')
    await input('模型连接方式', 'select').setValue('custom')
    const secret = wrapper.findAll('.content-secret-setting').find(item => item.text().includes('OpenAI API Key'))!
    await secret.get('button').trigger('click')
    await flushPromises()
    await button('保存设置').trigger('click')
    await flushPromises()
    expect(mocks.put).toHaveBeenLastCalledWith('/content/settings', { values: expect.any(Object), clear_secrets: ['app.openai_api_key'] })
  })
})
