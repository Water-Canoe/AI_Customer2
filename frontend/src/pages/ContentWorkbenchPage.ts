import { computed, defineComponent, h, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Collection,
  Delete,
  MagicStick,
  Plus,
  Refresh,
  Setting,
  Tickets,
  VideoPlay,
} from '@element-plus/icons-vue'

import { api } from '../shared/api'
import { isAccountFeatureReady } from '../shared/accounts'
import type { Dict, PlatformAccount } from '../shared/types'
import { createAutoSyncController, shouldReplaceDraft } from '../composables/autoSync'
import { draftStatusLabel, usePersistentDraft } from '../shared/usePersistentDraft'
import { useUnsavedSettings } from '../shared/useUnsavedSettings'
import AssetPicker from '../components/content/AssetPicker'
import { COMPACT_PAGE_SIZE, DEFAULT_PAGE_SIZE, ListPagination, paginateItems, type PageChange } from '../components/ui/ListPagination'
import { emptyState, sectionTitle } from '../components/ui/Workbench'
import './content.css'


const ASSET_SEGMENTS = [
  ['all', '全部'],
  ['video', '视频'],
  ['image', '图片'],
  ['background_music', '背景音乐'],
  ['voice_reference', '我的音色'],
] as const
const LIBRARY_TABS = ['drafts', 'jobs', 'outputs']

const defaultVideoDraft = () => ({
  video_subject: '',
  video_script: '',
  video_terms: '',
  video_source: 'local',
  video_aspect: '9:16',
  video_concat_mode: 'sequential',
  video_transition_mode: '',
  video_clip_duration: 5,
  match_materials_to_script: false,
  video_count: 1,
  video_language: 'zh-CN',
  voice_name: 'zh-CN-XiaoxiaoNeural-Female',
  voice_volume: 1,
  voice_rate: 1,
  bgm_type: '',
  bgm_volume: 0.2,
  subtitle_enabled: true,
  subtitle_position: 'bottom',
  custom_position: 70,
  font_name: 'MicrosoftYaHeiBold.ttc',
  text_fore_color: '#FFFFFF',
  text_background_color: true,
  rounded_subtitle_background: false,
  font_size: 60,
  stroke_color: '#000000',
  stroke_width: 1.5,
  n_threads: 2,
  paragraph_number: 1,
  video_script_prompt: '',
  custom_system_prompt: '',
})

type SettingField = {
  path: string
  label: string
  type?: 'text' | 'password' | 'number' | 'boolean' | 'list' | 'textarea'
  options?: Array<[string, string]>
}

const settingGroups: Array<{ title: string, fields: SettingField[] }> = [
  {
    title: '素材与基础处理',
    fields: [
      { path: 'app.video_source', label: '默认素材源', options: [['pexels', 'Pexels'], ['pixabay', 'Pixabay'], ['coverr', 'Coverr'], ['local', '本地素材']] },
      { path: 'app.pexels_api_keys', label: 'Pexels API Keys', type: 'list' },
      { path: 'app.pixabay_api_keys', label: 'Pixabay API Keys', type: 'list' },
      { path: 'app.coverr_api_keys', label: 'Coverr API Keys', type: 'list' },
      { path: 'app.edge_tts_timeout', label: 'Edge TTS超时秒数', type: 'number' },
      { path: 'app.subtitle_provider', label: '字幕来源', options: [['edge', 'TTS时间轴'], ['whisper', 'Whisper识别'], ['', '不生成字幕']] },
      { path: 'app.tls_verify', label: '校验TLS证书', type: 'boolean' },
      { path: 'app.video_codec', label: '视频编码器' },
      { path: 'app.ffmpeg_path', label: 'FFmpeg路径' },
      { path: 'app.imagemagick_path', label: 'ImageMagick路径' },
    ],
  },
  {
    title: 'AI供应商',
    fields: [
      { path: 'app.llm_provider', label: 'AI供应商', options: [['openai', 'OpenAI兼容'], ['moonshot', 'Moonshot'], ['deepseek', 'DeepSeek'], ['qwen', '通义千问'], ['gemini', 'Gemini'], ['ollama', 'Ollama'], ['oneapi', 'OneAPI'], ['aihubmix', 'AIHubMix'], ['aimlapi', 'AIML API'], ['minimax', 'MiniMax'], ['modelscope', 'ModelScope'], ['pollinations', 'Pollinations'], ['litellm', 'LiteLLM'], ['grok', 'Grok'], ['groq', 'Groq'], ['evolink', 'EvoLink'], ['volcengine', '火山方舟'], ['mimo', '小米MiMo']] },
      { path: 'app.openai_api_key', label: 'OpenAI API Key', type: 'password' },
      { path: 'app.openai_base_url', label: 'OpenAI Base URL' },
      { path: 'app.openai_model_name', label: 'OpenAI模型' },
      { path: 'app.moonshot_api_key', label: 'Moonshot API Key', type: 'password' },
      { path: 'app.moonshot_base_url', label: 'Moonshot Base URL' },
      { path: 'app.moonshot_model_name', label: 'Moonshot模型' },
      { path: 'app.deepseek_api_key', label: 'DeepSeek API Key', type: 'password' },
      { path: 'app.deepseek_base_url', label: 'DeepSeek Base URL' },
      { path: 'app.deepseek_model_name', label: 'DeepSeek模型' },
      { path: 'app.qwen_api_key', label: 'Qwen API Key', type: 'password' },
      { path: 'app.qwen_model_name', label: 'Qwen模型' },
      { path: 'app.gemini_api_key', label: 'Gemini API Key', type: 'password' },
      { path: 'app.gemini_model_name', label: 'Gemini模型' },
      { path: 'app.ollama_base_url', label: 'Ollama Base URL' },
      { path: 'app.ollama_model_name', label: 'Ollama模型' },
      { path: 'app.oneapi_api_key', label: 'OneAPI API Key', type: 'password' },
      { path: 'app.oneapi_base_url', label: 'OneAPI Base URL' },
      { path: 'app.oneapi_model_name', label: 'OneAPI模型' },
      { path: 'app.aihubmix_api_key', label: 'AIHubMix API Key', type: 'password' },
      { path: 'app.aihubmix_base_url', label: 'AIHubMix Base URL' },
      { path: 'app.aihubmix_model_name', label: 'AIHubMix模型' },
      { path: 'app.aimlapi_api_key', label: 'AIML API Key', type: 'password' },
      { path: 'app.aimlapi_base_url', label: 'AIML Base URL' },
      { path: 'app.aimlapi_model_name', label: 'AIML模型' },
      { path: 'app.minimax_api_key', label: 'MiniMax API Key', type: 'password' },
      { path: 'app.minimax_base_url', label: 'MiniMax Base URL' },
      { path: 'app.minimax_model_name', label: 'MiniMax模型' },
      { path: 'app.modelscope_api_key', label: 'ModelScope API Key', type: 'password' },
      { path: 'app.modelscope_base_url', label: 'ModelScope Base URL' },
      { path: 'app.modelscope_model_name', label: 'ModelScope模型' },
      { path: 'app.litellm_model_name', label: 'LiteLLM模型' },
      { path: 'app.pollinations_api_key', label: 'Pollinations API Key', type: 'password' },
      { path: 'app.pollinations_base_url', label: 'Pollinations Base URL' },
      { path: 'app.pollinations_model_name', label: 'Pollinations模型' },
      { path: 'app.grok_api_key', label: 'Grok API Key', type: 'password' },
      { path: 'app.grok_base_url', label: 'Grok Base URL' },
      { path: 'app.grok_model_name', label: 'Grok模型' },
      { path: 'app.groq_api_key', label: 'Groq API Key', type: 'password' },
      { path: 'app.groq_base_url', label: 'Groq Base URL' },
      { path: 'app.groq_model_name', label: 'Groq模型' },
      { path: 'app.evolink_api_key', label: 'EvoLink API Key', type: 'password' },
      { path: 'app.evolink_base_url', label: 'EvoLink Base URL' },
      { path: 'app.evolink_model_name', label: 'EvoLink模型' },
      { path: 'app.volcengine_api_key', label: '火山方舟 API Key', type: 'password' },
      { path: 'app.volcengine_base_url', label: '火山方舟 Base URL' },
      { path: 'app.volcengine_model_name', label: '火山方舟模型' },
      { path: 'app.mimo_api_key', label: 'MiMo API Key', type: 'password' },
      { path: 'app.mimo_base_url', label: 'MiMo Base URL' },
      { path: 'app.mimo_model_name', label: 'MiMo模型' },
    ],
  },
  {
    title: '语音与Whisper',
    fields: [
      { path: 'app.gemini_api_key', label: 'Gemini 文案与配音 API Key', type: 'password' },
      { path: 'app.gemini_base_url', label: 'Gemini Base URL' },
      { path: 'app.mimo_api_key', label: 'MiMo 文案与配音 API Key', type: 'password' },
      { path: 'app.mimo_base_url', label: 'MiMo Base URL' },
      { path: 'azure.speech_key', label: 'Azure Speech Key', type: 'password' },
      { path: 'azure.speech_region', label: 'Azure Speech Region' },
      { path: 'siliconflow.api_key', label: '硅基流动 API Key', type: 'password' },
      { path: 'elevenlabs.api_key', label: 'ElevenLabs API Key', type: 'password' },
      { path: 'elevenlabs.model_id', label: 'ElevenLabs模型' },
      { path: 'app.mimo_tts_model_name', label: 'MiMo TTS模型' },
      { path: 'app.mimo_tts_style_prompt', label: 'MiMo语音风格', type: 'textarea' },
      { path: 'chatterbox.base_url', label: 'Chatterbox Base URL' },
      { path: 'chatterbox.api_key', label: 'Chatterbox API Key', type: 'password' },
      { path: 'chatterbox.model_id', label: 'Chatterbox模型' },
      { path: 'chatterbox.voices', label: 'Chatterbox音色', type: 'list' },
      { path: 'whisper.model_size', label: 'Whisper模型' },
      { path: 'whisper.device', label: 'Whisper设备', options: [['CPU', 'CPU'], ['cuda', 'CUDA']] },
      { path: 'whisper.compute_type', label: 'Whisper计算精度' },
    ],
  },
  {
    title: 'TwelveLabs与代理',
    fields: [
      { path: 'app.twelvelabs_api_keys', label: 'TwelveLabs API Keys', type: 'list' },
      { path: 'app.twelvelabs_rerank_terms', label: '启用素材词语义重排', type: 'boolean' },
      { path: 'proxy.http', label: 'HTTP代理' },
      { path: 'proxy.https', label: 'HTTPS代理' },
    ],
  },
]

export default defineComponent({
  name: 'ContentWorkbenchPage',
  props: {
    refreshSeq: { type: Number, default: 0 },
  },
  setup(props) {
    const route = useRoute()
    const router = useRouter()
    const view = computed(() => String(route.name || 'content-create'))
    const assets = ref<Dict[]>([])
    const voiceProfiles = ref<Dict[]>([])
    const jobs = ref<Dict>({ items: [], total: 0, page: 1, page_size: DEFAULT_PAGE_SIZE })
    const publishAccounts = ref<PlatformAccount[]>([])
    const selectedJob = ref<Dict | null>(null)
    const outputs = ref<Dict>({ items: [], total: 0, page: 1, page_size: 12 })
    const savedDrafts = ref<Dict>({ items: [], total: 0, page: 1, page_size: 20 })
    const libraryTab = ref(route.query.job ? 'jobs' : LIBRARY_TABS.includes(String(route.query.tab)) ? String(route.query.tab) : 'outputs')
    const selectedOutputIds = ref<string[]>([])
    const materialPickerOpen = ref(false)
    const draftKey = ref(String(route.query.draft_id || crypto.randomUUID()))
    const restoringDraft = ref(false)
    const restoreError = ref('')
    const videoSubmission = ref<Dict | null>(null)
    let loadedDraftKey = ''
    let pageActive = true
    const settings = ref<Dict>({})
    const settingsDraft = ref<Dict>({})
    const settingsDirty = ref(false)
    const settingsSaving = ref(false)
    const clearSecrets = ref<string[]>([])
    const environment = ref<Dict>({})
    const videoDraft = ref<Dict>(defaultVideoDraft())
    const selectedAssetIds = ref<string[]>([])
    const selectedPublishAssetIds = ref<string[]>([])
    const autoPublish = ref<Dict>({ enabled: false, account_ids: [], output_scope: 'first', publish_strategy: 'immediate', scheduled_at: '' })
    const audioAssetId = ref('')
    const bgmAssetId = ref('')
    const assetFilter = ref({ search: '' })
    const assetSegment = ref('all')
    const assetPage = ref(1)
    const assetPageSize = ref(DEFAULT_PAGE_SIZE)
    const voicePage = ref(1)
    const recordSearch = ref('')
    const voiceProvider = ref('edge')
    const voiceMode = ref('tts')
    const voices = ref<string[]>([])
    const loading = ref(false)
    const uploading = ref(false)
    const draggedAssetId = ref('')
    const voiceReferenceScript = ref('')
    const voiceRecordingName = ref('我的克隆音色')
    const voiceRecordingSeconds = ref(0)
    const voiceRecording = ref(false)
    const voiceRecordingBlob = ref<Blob | null>(null)
    const voiceRecordingUrl = ref('')
    const voiceScriptLoading = ref(false)
    const voiceRecordingSaving = ref(false)
    let settingsMutationSeq = 0
    let voiceRecordingTimer = 0
    let mediaRecorder: MediaRecorder | null = null
    let mediaStream: MediaStream | null = null
    let mediaChunks: Blob[] = []

    const materialAssets = computed(() => assets.value.filter(item => ['video', 'image'].includes(String(item.asset_type))))
    const audioAssets = computed(() => assets.value.filter(item => String(item.asset_type) === 'audio'))
    const backgroundMusicAssets = computed(() => assets.value.filter(item => assetMatchesSegment(item, 'background_music')))
    const visibleAssets = computed(() => assets.value.filter(asset => assetMatchesSegment(asset, assetSegment.value)
      && String(asset.name || '').toLowerCase().includes(assetFilter.value.search.trim().toLowerCase())))
    const assetPagination = computed(() => paginateItems(visibleAssets.value, assetPage.value, assetPageSize.value))
    const selectedMaterials = computed(() => selectedAssetIds.value
      .map(id => materialAssets.value.find(item => item.id === id) || { id, name: '原素材已删除，请重新选择', deleted: true }) as Dict[])
    const activeJobCount = computed(() => Number(jobs.value.active || 0))
    const voiceRuntime = computed(() => environment.value.voice_models?.voxcpm2 || {})
    const voiceRuntimeJob = computed(() => voiceRuntime.value.install_job || {})
    const voiceRuntimeInstalling = computed(() => ['queued', 'running'].includes(String(voiceRuntimeJob.value.status || '')))
    const persistentDraft = usePersistentDraft({
      kind: 'video', key: draftKey,
      getPayload: () => ({ subject: videoDraft.value.video_subject, params: videoDraft.value,
        asset_ids: selectedAssetIds.value, audio_asset_id: audioAssetId.value, bgm_asset_id: bgmAssetId.value,
        voice_provider: voiceProvider.value, voice_mode: voiceMode.value, publish_plan: autoPublish.value,
        ...(videoSubmission.value ? { return_context: videoSubmission.value } : {}) }),
      applyPayload: payload => {
        videoDraft.value = { ...defaultVideoDraft(), ...payload.params }
        selectedAssetIds.value = payload.asset_ids || []
        audioAssetId.value = payload.audio_asset_id || ''
        bgmAssetId.value = payload.bgm_asset_id || ''
        voiceProvider.value = payload.voice_provider || 'edge'
        voiceMode.value = payload.voice_mode || (payload.audio_asset_id ? 'file' : payload.voice_provider === 'voxcpm2' ? 'clone' : 'tts')
        autoPublish.value = { enabled: false, account_ids: [], output_scope: 'first', publish_strategy: 'immediate', scheduled_at: '', ...payload.publish_plan }
        videoSubmission.value = payload.return_context?.query?.job ? payload.return_context : null
      },
    })
    useUnsavedSettings(() => view.value === 'content-settings' && settingsDirty.value)

    const refresh = createAutoSyncController({
      interval: () => 3000,
      sync: async reason => {
        if (reason === 'route') {
          try { await loadPage() }
          catch (error: any) { ElMessage.error(error?.response?.data?.detail || '内容页面加载失败，请重试') }
          return
        }
        const requests: Promise<unknown>[] = []
        if (activeJobCount.value && ['content-create', 'content-records'].includes(view.value)) requests.push(loadJobs(false))
        if (view.value === 'content-records' && libraryTab.value === 'outputs') requests.push(loadOutputs())
        if (view.value === 'content-settings' && voiceRuntimeInstalling.value) requests.push(loadEnvironment())
        // 等所有后台查询结束后再允许下一轮，某个查询失败也不会叠加请求。
        await Promise.allSettled(requests)
      },
    })

    onMounted(() => {
      refresh.start()
      void refresh.trigger('route')
    })
    onUnmounted(() => {
      pageActive = false
      refresh.stop()
      window.clearInterval(voiceRecordingTimer)
      if (mediaRecorder?.state === 'recording') mediaRecorder.stop()
      mediaStream?.getTracks().forEach(track => track.stop())
      if (voiceRecordingUrl.value) URL.revokeObjectURL(voiceRecordingUrl.value)
    })
    watch(view, (next, previous) => {
      if (previous === 'content-settings' && next !== 'content-settings') {
        settingsDraft.value = JSON.parse(JSON.stringify(settings.value))
        settingsDirty.value = false
        clearSecrets.value = []
        settingsMutationSeq += 1
      }
    })
    watch(() => route.fullPath, () => void refresh.trigger('route'))
    watch(() => props.refreshSeq, () => void refresh.trigger('route'))
    watch(assetSegment, () => { assetPage.value = 1; voicePage.value = 1 })
    onBeforeRouteUpdate(async () => {
      // 同一页面组件切换路由时不会触发离页钩子，仍须完成读取或保存。
      if (restoringDraft.value) return false
      if (view.value === 'content-create' && String(route.query.draft_id || draftKey.value) === draftKey.value) {
        try { await persistentDraft.flush() } catch { return false }
      }
      return true
    })

    async function loadPage() {
      if (view.value === 'content-create') {
        await Promise.all([loadAssets(), loadVoiceProfiles(), loadJobs(false), loadPublishAccounts(), restoreVideoDraft()])
        if (pageActive && !videoSubmission.value && voiceMode.value !== 'file') await loadVoices()
      }
      else if (view.value === 'content-assets') await Promise.all([loadAssets(), loadVoiceProfiles(), loadPublishAccounts()])
      else if (view.value === 'content-records') {
        libraryTab.value = route.query.job ? 'jobs' : LIBRARY_TABS.includes(String(route.query.tab)) ? String(route.query.tab) : 'outputs'
        if (!route.query.job) selectedJob.value = null
        await loadLibrary()
      }
      else await Promise.all([loadSettings(), loadEnvironment()])
    }

    async function restoreVideoDraft() {
      const requestedKey = String(route.query.draft_id || draftKey.value)
      if (loadedDraftKey === requestedKey) return
      if (restoringDraft.value) return
      const retryDefaults = Boolean(restoreError.value && !persistentDraft.error.value)
      restoringDraft.value = true
      restoreError.value = ''
      const ownsRestore = () => pageActive && view.value === 'content-create' && requestedKey === draftKey.value && String(route.query.draft_id || requestedKey) === requestedKey
      try {
        if (loadedDraftKey || retryDefaults) await persistentDraft.flush()
        if (loadedDraftKey) resetVideoFields()
        draftKey.value = requestedKey
        const record = await persistentDraft.load()
        if (!ownsRestore()) return
        if (!record.exists) {
          const { data } = await api.get('/content/settings')
          if (!ownsRestore()) return
          if (persistentDraft.status.value === 'idle') videoDraft.value.video_source = data.app?.video_source || 'local'
        }
        loadedDraftKey = requestedKey
        restoringDraft.value = false
        if (!route.query.draft_id) await router.replace({ path: '/content-create', query: { draft_id: requestedKey } })
      } catch (error: any) {
        if (ownsRestore()) restoreError.value = error?.response?.data?.detail || '创作草稿读取失败，请重试'
        throw error
      } finally {
        restoringDraft.value = false
      }
    }

    async function loadLibrary() {
      if (libraryTab.value === 'drafts') return loadSavedDrafts()
      if (libraryTab.value === 'jobs') {
        await loadJobs(true)
        return
      }
      return loadOutputs()
    }

    async function loadSavedDrafts() {
      const { data } = await api.get('/drafts/video', { params: { page: savedDrafts.value.page, page_size: savedDrafts.value.page_size, search: recordSearch.value } })
      savedDrafts.value = data
    }

    async function loadOutputs() {
      const { data } = await api.get('/content/outputs', { params: { page: outputs.value.page, page_size: outputs.value.page_size, search: recordSearch.value } })
      outputs.value = data
    }

    async function loadAssets() {
      const { data } = await api.get('/content/assets')
      assets.value = data
    }

    async function loadVoiceProfiles() {
      const { data } = await api.get('/content/voice-profiles')
      voiceProfiles.value = data
    }

    async function loadJobs(selectFirst = false) {
      const { data } = await api.get('/content/video-jobs', { params: { page: jobs.value.page || 1, page_size: jobs.value.page_size || 20, search: view.value === 'content-records' ? recordSearch.value : '' } })
      jobs.value = data
      if (selectedJob.value) {
        const found = data.items.find((item: Dict) => item.id === selectedJob.value?.id)
        if (found) selectedJob.value = found
      }
      if (selectFirst && !selectedJob.value && data.items.length) selectedJob.value = data.items[0]
      if (view.value === 'content-records' && route.query.job) {
        const detail = await api.get(`/content/video-jobs/${encodeURIComponent(String(route.query.job))}`)
        selectedJob.value = detail.data
      }
    }

    async function loadPublishAccounts() {
      const { data } = await api.get<PlatformAccount[]>('/accounts', { params: { feature: 'publish' } })
      publishAccounts.value = data
    }

    async function loadSettings() {
      const requestSeq = settingsMutationSeq
      const { data } = await api.get('/content/settings')
      if (!shouldReplaceDraft(settingsDirty.value, settingsSaving.value, requestSeq, settingsMutationSeq)) return
      settings.value = data
      settingsDraft.value = JSON.parse(JSON.stringify(data))
    }

    async function loadEnvironment() {
      const { data } = await api.get('/content/environment-check')
      environment.value = data
    }

    async function installVoiceRuntime() {
      try {
        await api.post('/content/voice-runtime/install')
        ElMessage.success('音色克隆组件已加入下载队列')
        await loadEnvironment()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '音色克隆组件安装失败')
      }
    }

    async function cancelVoiceRuntimeInstall() {
      const jobId = String(voiceRuntimeJob.value.id || '')
      if (!jobId) return
      try {
        await api.post(`/runtime/jobs/${jobId}/cancel`)
        ElMessage.success('已请求取消组件安装')
        await loadEnvironment()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '取消安装失败')
      }
    }

    async function loadVoices() {
      try {
        const { data } = await api.get('/content/voices', { params: { provider: voiceProvider.value } })
        voices.value = data
        if (data.length && !data.includes(videoDraft.value.voice_name)) videoDraft.value.voice_name = data[0]
        if (!data.length && voiceProvider.value === 'voxcpm2') videoDraft.value.voice_name = ''
      } catch (error: any) {
        voices.value = []
        ElMessage.error(error?.response?.data?.detail || '音色列表加载失败')
      }
    }

    async function uploadAssets(event: Event) {
      const input = event.target as HTMLInputElement
      const files = Array.from(input.files || [])
      if (!files.length) return
      uploading.value = true
      try {
        const form = new FormData()
        files.forEach(file => form.append('files', file))
        const purpose = ['background_music', 'voice_reference'].includes(assetSegment.value) ? assetSegment.value : ''
        const { data } = await api.post('/content/assets/import', form, { params: { purpose } })
        const duplicates = data.filter((item: Dict) => item.duplicate).length
        ElMessage.success(`已导入 ${data.length - duplicates} 项${duplicates ? `，跳过 ${duplicates} 项重复文件` : ''}`)
        await loadAssets()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '内容资产导入失败')
      } finally {
        uploading.value = false
        input.value = ''
      }
    }

    async function renameAsset(asset: Dict) {
      try {
        const result = await ElMessageBox.prompt('输入新的资产名称', '重命名资产', { inputValue: String(asset.name || '') })
        await api.patch(`/content/assets/${asset.id}`, { name: result.value })
        ElMessage.success('资产名称已更新')
        await loadAssets()
      } catch (error: any) {
        if (isUserCancel(error)) return
        ElMessage.error(error?.response?.data?.detail || '重命名失败')
      }
    }

    async function deleteAsset(asset: Dict) {
      try {
        const linkedProfiles = voiceProfiles.value.filter(profile => profile.reference_asset_id === asset.id)
        const message = linkedProfiles.length
          ? `确认删除“${asset.name}”？关联的 ${linkedProfiles.length} 个克隆音色也会一并删除。`
          : `确认删除“${asset.name}”？正在使用的资产不会被删除。`
        await ElMessageBox.confirm(message, '删除内容资产', { type: 'warning' })
        for (const profile of linkedProfiles) await api.delete(`/content/voice-profiles/${profile.id}`)
        await api.delete(`/content/assets/${asset.id}`)
        selectedPublishAssetIds.value = selectedPublishAssetIds.value.filter(id => id !== String(asset.id))
        ElMessage.success('资产已删除')
        await Promise.all([loadAssets(), loadVoiceProfiles()])
      } catch (error: any) {
        if (isUserCancel(error)) return
        ElMessage.error(error?.response?.data?.detail || '资产删除失败')
      }
    }

    async function generateVoiceReferenceScript() {
      voiceScriptLoading.value = true
      try {
        const { data } = await api.post('/content/voice-reference-script')
        voiceReferenceScript.value = String(data.script || '')
        ElMessage.success('朗读文案已生成，请按原文自然朗读')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '朗读文案生成失败')
      } finally {
        voiceScriptLoading.value = false
      }
    }

    async function startVoiceRecording() {
      if (!voiceReferenceScript.value.trim()) return ElMessage.warning('请先生成或填写朗读文案')
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') return ElMessage.error('当前浏览器不支持麦克风录音')
      try {
        clearVoiceRecording()
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
        const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(type => MediaRecorder.isTypeSupported(type)) || ''
        mediaRecorder = mimeType ? new MediaRecorder(mediaStream, { mimeType }) : new MediaRecorder(mediaStream)
        mediaChunks = []
        mediaRecorder.ondataavailable = event => { if (event.data.size) mediaChunks.push(event.data) }
        mediaRecorder.onstop = () => {
          const type = mediaRecorder?.mimeType || mimeType || 'audio/webm'
          voiceRecordingBlob.value = new Blob(mediaChunks, { type })
          voiceRecordingUrl.value = URL.createObjectURL(voiceRecordingBlob.value)
          mediaStream?.getTracks().forEach(track => track.stop())
          mediaStream = null
          mediaRecorder = null
        }
        mediaRecorder.start(250)
        voiceRecording.value = true
        voiceRecordingSeconds.value = 0
        voiceRecordingTimer = window.setInterval(() => {
          voiceRecordingSeconds.value += 1
          if (voiceRecordingSeconds.value >= 120) stopVoiceRecording()
        }, 1000)
      } catch (error: any) {
        mediaStream?.getTracks().forEach(track => track.stop())
        mediaStream = null
        ElMessage.error(error?.name === 'NotAllowedError' ? '未获得麦克风权限，请在浏览器中允许录音' : '麦克风启动失败')
      }
    }

    function stopVoiceRecording() {
      window.clearInterval(voiceRecordingTimer)
      voiceRecording.value = false
      if (mediaRecorder?.state === 'recording') mediaRecorder.stop()
    }

    function clearVoiceRecording() {
      if (voiceRecordingUrl.value) URL.revokeObjectURL(voiceRecordingUrl.value)
      voiceRecordingUrl.value = ''
      voiceRecordingBlob.value = null
      voiceRecordingSeconds.value = 0
    }

    async function saveVoiceRecording() {
      const blob = voiceRecordingBlob.value
      const name = voiceRecordingName.value.trim()
      const script = voiceReferenceScript.value.trim()
      if (!blob) return ElMessage.warning('请先完成录音')
      if (!name) return ElMessage.warning('请填写录音名称')
      if (!script) return ElMessage.warning('朗读文案不能为空')
      try {
        await ElMessageBox.confirm('我确认录音是本人声音或已获得声音使用授权，并同意用于AI声音克隆。', '声音授权确认', { confirmButtonText: '确认并保存', type: 'warning' })
        voiceRecordingSaving.value = true
        const extension = recordingExtension(blob.type)
        const filename = `${name.replace(/[\\/:*?"<>|]/g, '-').replace(/\.[^.]+$/, '')}.${extension}`
        const form = new FormData()
        form.append('files', new File([blob], filename, { type: blob.type || 'audio/webm' }))
        const { data } = await api.post('/content/assets/import', form, { params: { purpose: 'voice_reference' } })
        const asset = data[0]
        await api.post('/content/voice-profiles', {
          name,
          provider: 'voxcpm2',
          reference_asset_id: asset.id,
          prompt_text: script,
          style_prompt: '',
          consent_confirmed: true,
        })
        clearVoiceRecording()
        await Promise.all([loadAssets(), loadVoiceProfiles()])
        ElMessage.success('录音和克隆音色已保存')
      } catch (error: any) {
        if (isUserCancel(error)) return
        ElMessage.error(error?.response?.data?.detail || '录音保存失败')
      } finally {
        voiceRecordingSaving.value = false
      }
    }

    async function createVoiceProfile(asset: Dict) {
      try {
        // 参考文字可选：填写时走VoxCPM2高保真模式，留空仍可普通克隆。
        const name = await ElMessageBox.prompt('输入音色名称', '创建克隆音色', { inputValue: String(asset.name || '').replace(/\.[^.]+$/, '') })
        const transcript = await ElMessageBox.prompt('输入参考音频中的准确文字；不填写则使用普通克隆模式', '参考音频文字', { inputValue: '' })
        await ElMessageBox.confirm('我确认已获得该声音的使用授权，并同意仅用于合法的AI合成内容。', '声音授权确认', { confirmButtonText: '确认并创建', type: 'warning' })
        await api.post('/content/voice-profiles', {
          name: name.value,
          provider: 'voxcpm2',
          reference_asset_id: asset.id,
          prompt_text: transcript.value || '',
          style_prompt: '',
          consent_confirmed: true,
        })
        ElMessage.success('克隆音色已创建')
        await Promise.all([loadAssets(), loadVoiceProfiles()])
      } catch (error: any) {
        if (isUserCancel(error)) return
        ElMessage.error(error?.response?.data?.detail || '克隆音色创建失败')
      }
    }

    async function renameVoiceProfile(profile: Dict) {
      try {
        const result = await ElMessageBox.prompt('输入新的音色名称', '重命名音色', { inputValue: String(profile.name || '') })
        const sharedReference = voiceProfiles.value.some(item => item.id !== profile.id && item.reference_asset_id === profile.reference_asset_id)
        await api.patch(`/content/voice-profiles/${profile.id}`, { name: result.value })
        if (!sharedReference && profile.reference_asset?.id) {
          await api.patch(`/content/assets/${profile.reference_asset.id}`, { name: fileNameWithExtension(result.value, profile.reference_asset.name) })
        }
        await Promise.all([loadAssets(), loadVoiceProfiles()])
      } catch (error: any) {
        if (isUserCancel(error)) return
        ElMessage.error(error?.response?.data?.detail || '音色修改失败')
      }
    }

    async function deleteVoiceProfile(profile: Dict) {
      try {
        const sharedReference = voiceProfiles.value.some(item => item.id !== profile.id && item.reference_asset_id === profile.reference_asset_id)
        await ElMessageBox.confirm(`确认删除音色“${profile.name}”？${sharedReference ? '其参考录音仍被其他音色使用，将继续保留。' : '关联的参考录音也会一并删除。'}`, '删除克隆音色', { type: 'warning' })
        await api.delete(`/content/voice-profiles/${profile.id}`)
        if (!sharedReference && profile.reference_asset?.id) await api.delete(`/content/assets/${profile.reference_asset.id}`)
        await Promise.all([loadAssets(), loadVoiceProfiles()])
        if (videoDraft.value.voice_name === `voxcpm2:${profile.id}`) videoDraft.value.voice_name = ''
      } catch (error: any) {
        if (isUserCancel(error)) return
        ElMessage.error(error?.response?.data?.detail || '音色删除失败')
      }
    }

    async function generateScript() {
      if (!String(videoDraft.value.video_subject || '').trim()) return ElMessage.warning('请先填写视频主题')
      loading.value = true
      try {
        const { data } = await api.post('/content/scripts', {
          video_subject: videoDraft.value.video_subject,
          video_language: videoDraft.value.video_language,
          paragraph_number: Number(videoDraft.value.paragraph_number || 1),
          video_script_prompt: videoDraft.value.video_script_prompt,
          custom_system_prompt: videoDraft.value.custom_system_prompt,
        })
        videoDraft.value.video_script = data.video_script
        ElMessage.success('视频文案已生成')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '视频文案生成失败')
      } finally {
        loading.value = false
      }
    }

    async function generateTerms() {
      if (!String(videoDraft.value.video_script || '').trim()) return ElMessage.warning('请先生成或填写视频文案')
      loading.value = true
      try {
        const { data } = await api.post('/content/terms', {
          video_subject: videoDraft.value.video_subject,
          video_script: videoDraft.value.video_script,
          amount: 8,
          match_materials_to_script: Boolean(videoDraft.value.match_materials_to_script),
        })
        videoDraft.value.video_terms = (data.video_terms || []).join(', ')
        ElMessage.success('素材关键词已生成')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '素材关键词生成失败')
      } finally {
        loading.value = false
      }
    }

    async function createVideo() {
      if (restoringDraft.value || videoSubmission.value) return
      const draft = JSON.parse(JSON.stringify(videoDraft.value))
      if (!String(draft.video_subject || '').trim()) return ElMessage.warning('请填写视频主题')
      if (draft.video_source === 'local' && !selectedAssetIds.value.length) return ElMessage.warning('本地素材模式至少选择一项内容资产')
      if (voiceMode.value === 'file' && !audioAssetId.value) return ElMessage.warning('请先导入并选择配音文件')
      if (voiceProvider.value === 'voxcpm2' && !audioAssetId.value && !String(draft.voice_name || '').trim()) return ElMessage.warning('请先选择一个克隆音色')
      if (autoPublish.value.enabled && !autoPublish.value.account_ids.length) return ElMessage.warning('自动发布至少选择一个已登录账号')
      draft.video_transition_mode = draft.video_transition_mode || null
      loading.value = true
      let created = false
      try {
        await persistentDraft.flush()
        const { data } = await api.post('/content/video-jobs', {
          draft_key: draftKey.value,
          params: draft,
          asset_ids: selectedAssetIds.value,
          audio_asset_id: audioAssetId.value,
          bgm_asset_id: bgmAssetId.value,
          publish: autoPublish.value,
        })
        created = true
        videoSubmission.value = { route: '/content-records', query: { job: data.id }, selection: [] }
        ElMessage.success('视频任务已加入生成队列')
        selectedJob.value = data
        await finishVideoSubmission()
      } catch (error: any) {
        ElMessage.error(created ? '视频任务已创建，但草稿清理或页面刷新失败，请勿重复提交' : error?.response?.data?.detail || '视频任务创建失败')
      } finally {
        loading.value = false
      }
    }

    function resetVideoFields() {
      videoDraft.value = defaultVideoDraft()
      selectedAssetIds.value = []
      audioAssetId.value = ''
      bgmAssetId.value = ''
      autoPublish.value = { enabled: false, account_ids: [], output_scope: 'first', publish_strategy: 'immediate', scheduled_at: '' }
      voiceProvider.value = 'edge'
      voiceMode.value = 'tts'
      videoSubmission.value = null
    }

    async function finishVideoSubmission() {
      await persistentDraft.clear()
      loadedDraftKey = ''
      draftKey.value = crypto.randomUUID()
      resetVideoFields()
      await router.replace({ path: '/content-create', query: { draft_id: draftKey.value } })
    }

    async function retryVideoCleanup() {
      loading.value = true
      try { await finishVideoSubmission() }
      catch { ElMessage.error('视频任务已创建，草稿仍未清理，可重试清理或查看生成任务') }
      finally { loading.value = false }
    }

    async function clearVideoForm() {
      if (restoringDraft.value) return
      try {
        await ElMessageBox.confirm('清空当前草稿的文案、素材和参数？已经生成的成品会继续保留。', '清空草稿', { type: 'warning' })
        await persistentDraft.clear()
        loadedDraftKey = ''
        draftKey.value = crypto.randomUUID()
        resetVideoFields()
        await router.replace({ path: '/content-create', query: { draft_id: draftKey.value } })
      } catch (error: any) {
        if (!isUserCancel(error)) ElMessage.error(error?.response?.data?.detail || '清空草稿失败')
      }
    }

    async function newVideoDraft() {
      if (restoringDraft.value) return
      try {
        await persistentDraft.flush()
        loadedDraftKey = ''
        draftKey.value = crypto.randomUUID()
        resetVideoFields()
        await router.push({ path: '/content-create', query: { draft_id: draftKey.value } })
      } catch (error: any) { ElMessage.error(error?.response?.data?.detail || '草稿保存失败') }
    }

    async function editJobAsDraft(job: Dict) {
      const key = crypto.randomUUID()
      const linked = job.assets || []
      try {
        await api.put(`/drafts/video/${key}`, { payload: {
          subject: job.subject, params: job.params,
          asset_ids: linked.filter((asset: Dict) => asset.role === 'material').map((asset: Dict) => asset.id),
          audio_asset_id: linked.find((asset: Dict) => asset.role === 'audio')?.id || '',
          bgm_asset_id: linked.find((asset: Dict) => asset.role === 'bgm')?.id || '',
          voice_provider: inferVoiceProvider(String(job.params?.voice_name || '')),
          voice_mode: linked.some((asset: Dict) => asset.role === 'audio') ? 'file' : String(job.params?.voice_name || '').startsWith('voxcpm2:') ? 'clone' : 'tts',
          publish_plan: { enabled: false, account_ids: [], output_scope: 'first', publish_strategy: 'immediate', scheduled_at: '' },
        } })
        await router.push({ path: '/content-create', query: { draft_id: key } })
      } catch (error: any) { ElMessage.error(error?.response?.data?.detail || '无法建立编辑草稿') }
    }

    async function removeSavedDraft(key: string) {
      try {
        await ElMessageBox.confirm('删除这个未提交的创作草稿？素材原文件和已生成视频不会删除。', '删除草稿', { type: 'warning' })
        await api.delete(`/drafts/video/${encodeURIComponent(key)}`)
        await loadSavedDrafts()
      } catch (error: any) { if (!isUserCancel(error)) ElMessage.error(error?.response?.data?.detail || '删除草稿失败') }
    }

    async function cancelJob(id: string) {
      try {
        await api.post(`/content/video-jobs/${id}/cancel`)
        ElMessage.success('已发送取消请求')
        await loadJobs(false)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '取消失败')
      }
    }

    async function retryJob(id: string) {
      try {
        await api.post(`/content/video-jobs/${id}/retry`)
        ElMessage.success('视频任务已重新排队')
        await loadJobs(false)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '重试失败')
      }
    }

    async function archiveJob(id: string) {
      try {
        const job = (jobs.value.items || []).find((item: Dict) => String(item.id) === id)
        await ElMessageBox.confirm(`确认删除“${job?.subject || '该生成记录'}”？记录会从页面隐藏，成品文件继续保留。`, '删除生成记录', { type: 'warning' })
        await api.delete(`/content/video-jobs/${id}`)
        ElMessage.success('生成记录已删除')
        if (selectedJob.value?.id === id) selectedJob.value = null
        await loadJobs(true)
      } catch (error: any) {
        if (isUserCancel(error)) return
        ElMessage.error(error?.response?.data?.detail || '归档失败')
      }
    }

    async function renameJob(job: Dict) {
      try {
        const result = await ElMessageBox.prompt('输入新的主题名称', '编辑生成内容', { inputValue: String(job.subject || '') })
        const { data } = await api.patch(`/content/video-jobs/${job.id}`, { subject: result.value })
        selectedJob.value = data
        ElMessage.success('主题名称已更新')
        await loadJobs(false)
      } catch (error: any) {
        if (isUserCancel(error)) return
        ElMessage.error(error?.response?.data?.detail || '主题修改失败')
      }
    }

    function openPublishSettings(output: Dict) {
      void router.push({ path: '/content-publish', query: { output_ids: String(output.output_id || output.id) } })
    }

    async function publishAsset(asset: Dict) {
      openAssetPublishSettings([String(asset.id)])
    }

    function openAssetPublishSettings(assetIds: string[]) {
      if (!assetIds.length) return ElMessage.warning('请先选择图片')
      void router.push({ path: '/content-publish', query: { asset_ids: assetIds.join(',') } })
    }

    async function updateOutputStatus(job: Dict, output: Dict, uploadStatus: string) {
      try {
        await api.patch(`/content/outputs/${output.output_id || output.id}/status`, { upload_status: uploadStatus })
        await loadOutputs()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '上传状态修改失败')
      }
    }

    async function saveSettings() {
      settingsSaving.value = true
      const saveSeq = ++settingsMutationSeq
      try {
        // 只提交可编辑配置，公开的连接摘要和凭据状态不回写服务端。
        const values: Dict = { model_connection_mode: settingsDraft.value.model_connection_mode || 'shared' }
        settingGroups.flatMap(group => group.fields).forEach(field => {
          const value = getPath(settingsDraft.value, field.path)
          if (value !== undefined) setPath(values, field.path, value)
        })
        const clearing = [...clearSecrets.value]
        const { data } = await api.put('/content/settings', { values, ...(clearing.length ? { clear_secrets: clearing } : {}) })
        settings.value = data
        clearSecrets.value = clearSecrets.value.filter(path => !clearing.includes(path))
        if (saveSeq === settingsMutationSeq) {
          settingsDraft.value = JSON.parse(JSON.stringify(data))
          settingsDirty.value = false
        }
        ElMessage.success('内容设置已保存')
        await loadEnvironment()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '内容设置保存失败')
      } finally {
        settingsSaving.value = false
      }
    }

    function toggleMaterial(id: string) {
      if (selectedAssetIds.value.includes(id)) selectedAssetIds.value = selectedAssetIds.value.filter(value => value !== id)
      else selectedAssetIds.value = [...selectedAssetIds.value, id]
    }

    function reorderMaterial(sourceId: string, targetId: string) {
      if (!sourceId || sourceId === targetId) return
      const ordered = [...selectedAssetIds.value]
      const source = ordered.indexOf(sourceId)
      const target = ordered.indexOf(targetId)
      if (source < 0 || target < 0) return
      ordered.splice(source, 1)
      ordered.splice(target, 0, sourceId)
      selectedAssetIds.value = ordered
    }

    function renderCreatePage() {
      return h('div', { class: 'content-edit-layout' }, [
        h('section', { class: 'workspace-panel content-editor' }, [
          h('div', { class: 'workspace-heading' }, [h('div', [h('h2', '创作视频'), h('p', { class: 'workspace-note' }, '先准备内容和素材，再生成成品。草稿会自动保存。')]), h('button', { class: 'secondary-action', disabled: restoringDraft.value || loading.value, onClick: newVideoDraft }, '新建草稿')]),
          h('section', { class: 'content-editor-section' }, [
            h('h3', '1. 文案与画面'),
            formInput('作品主题', videoDraft.value.video_subject, value => videoDraft.value.video_subject = value),
            formTextarea('视频文案', videoDraft.value.video_script, value => videoDraft.value.video_script = value),
            h('div', { class: 'workspace-toolbar' }, [h('button', { class: 'secondary-action', disabled: loading.value, onClick: generateScript }, 'AI生成文案'), h('span', { class: 'workspace-note' }, '也可以直接填写；留空时会按主题调用内容 AI。')]),
            h('div', { class: 'content-editor-grid' }, [
              formSelect('画面比例', videoDraft.value.video_aspect, [['9:16', '竖屏 9:16'], ['16:9', '横屏 16:9'], ['1:1', '方形 1:1']], value => videoDraft.value.video_aspect = value),
              formSelect('素材来源', videoDraft.value.video_source, [['local', '我的素材'], ['pexels', 'Pexels'], ['pixabay', 'Pixabay'], ['coverr', 'Coverr']], value => videoDraft.value.video_source = value),
            ]),
            videoDraft.value.video_source === 'local' ? h('div', { class: 'content-material-summary' }, [
              h('div', { class: 'workspace-toolbar' }, [h('strong', `已选 ${selectedAssetIds.value.length} 项素材`), h('button', { class: 'secondary-action', onClick: () => materialPickerOpen.value = true }, '选择或导入素材')]),
              selectedMaterials.value.map((asset, index) => h('div', { class: 'content-selected-material', draggable: true, onDragstart: () => draggedAssetId.value = String(asset.id), onDragover: (event: DragEvent) => event.preventDefault(), onDrop: () => reorderMaterial(draggedAssetId.value, String(asset.id)) }, [
                h('strong', String(index + 1)), asset.thumbnail_url ? h('img', { src: asset.thumbnail_url, alt: '' }) : null,
                h('span', { title: String(asset.name) }, String(asset.name)),
                h('button', { class: 'text-icon-button', onClick: () => toggleMaterial(String(asset.id)) }, '移除'),
              ])),
              !selectedAssetIds.value.length ? h('p', { class: 'workspace-note' }, '选择视频或图片，按展示顺序拼接；可拖动已选素材调整顺序。') : null,
            ]) : h('details', { class: 'content-fold', open: true }, [h('summary', '在线素材关键词'), h('div', { class: 'content-fold-body' }, [formTextarea('素材关键词', videoDraft.value.video_terms, value => videoDraft.value.video_terms = value, 'field-full'), h('button', { class: 'secondary-action', disabled: loading.value, onClick: generateTerms }, 'AI生成素材词')])]),
          ]),
          h('section', { class: 'content-editor-section' }, [
            h('h3', '2. 配音与字幕'),
            h('div', { class: 'content-editor-grid' }, [
              formSelect('配音方式', voiceMode.value, [['tts', '标准配音'], ['clone', '我的克隆音色'], ['file', '使用配音文件']], value => {
                voiceMode.value = value
                if (value === 'file') audioAssetId.value = ''
                else { audioAssetId.value = ''; voiceProvider.value = value === 'clone' ? 'voxcpm2' : 'edge'; void loadVoices() }
              }),
              voiceMode.value === 'file' ? formSelect('配音文件', audioAssetId.value, [['', '请选择已导入的音频'], ...audioAssets.value.map((item): [string, string] => [String(item.id), String(item.name)])], value => audioAssetId.value = value)
                : voices.value.length || voiceProvider.value === 'voxcpm2'
                ? formSelect('配音音色', videoDraft.value.voice_name, voices.value.length ? voices.value.map(value => [value, voiceOptionLabel(value)]) : [['', '请先在素材与音色中创建音色']], value => videoDraft.value.voice_name = value)
                : formInput('配音音色', videoDraft.value.voice_name, value => videoDraft.value.voice_name = value),
              formSelect('背景音乐', bgmAssetId.value, [['', '不使用'], ...backgroundMusicAssets.value.map((item): [string, string] => [String(item.id), String(item.name)])], value => bgmAssetId.value = value),
              formToggle('生成字幕', videoDraft.value.subtitle_enabled, value => videoDraft.value.subtitle_enabled = value),
            ]),
            !audioAssets.value.length ? h('p', { class: 'workspace-note' }, '需要已有配音文件时，可在素材与音色页面导入音频。') : null,
            audioAssetId.value ? h('p', { class: 'workspace-note' }, '配音文件不经过 TTS；需要字幕时，请在内容设置选择 Whisper 识别。') : null,
            voiceMode.value !== 'file' ? h('details', { class: 'content-fold' }, [h('summary', '配音供应商'), h('div', { class: 'content-fold-body' }, [formSelect('供应商', voiceProvider.value, [['edge', 'Edge TTS'], ['voxcpm2', '音色克隆'], ['azure-v2', 'Azure Speech'], ['siliconflow', '硅基流动'], ['gemini', 'Gemini TTS'], ['mimo', 'MiMo TTS'], ['elevenlabs', 'ElevenLabs'], ['chatterbox', 'Chatterbox']], value => { audioAssetId.value = ''; voiceProvider.value = value; voiceMode.value = value === 'voxcpm2' ? 'clone' : 'tts'; void loadVoices() })])]) : null,
          ]),
          renderAdvancedVideoOptions(),
          renderAutoPublishOptions(),
          h('div', { class: 'content-submit-bar' }, [
            h('div', [h('span', { class: ['content-save-state', persistentDraft.error.value || restoreError.value ? 'is-error' : ''] }, videoSubmission.value ? '该草稿已提交，生成任务不会再次创建。' : persistentDraft.error.value || restoreError.value || (restoringDraft.value ? '读取创作配置中…' : draftStatusLabel(persistentDraft.status.value))), !videoSubmission.value && (persistentDraft.error.value || restoreError.value) ? h('button', { class: 'text-icon-button', disabled: restoringDraft.value, onClick: () => (loadedDraftKey ? persistentDraft.flush() : restoreVideoDraft()).catch(() => undefined) }, loadedDraftKey ? '重试保存草稿' : '重试读取草稿') : null]),
            videoSubmission.value ? h('div', { class: 'workspace-toolbar' }, [h('button', { class: 'secondary-action', onClick: () => router.push({ path: '/content-records', query: videoSubmission.value!.query }) }, '查看已创建生成任务'), h('button', { class: 'primary-action', disabled: loading.value, onClick: retryVideoCleanup }, '重试清理已提交草稿')])
              : h('div', { class: 'workspace-toolbar' }, [h('button', { class: 'secondary-action', disabled: loading.value || restoringDraft.value, onClick: clearVideoForm }, '清空草稿'), h('button', { class: 'primary-action', disabled: loading.value || restoringDraft.value, onClick: createVideo }, loading.value ? '提交中…' : '生成视频')]),
          ]),
        ]),
        h('aside', { class: 'workspace-panel content-editor-summary' }, [
          h('h3', '本次创作'),
          h('dl', [h('dt', '素材'), h('dd', `${selectedAssetIds.value.length} 项`), h('dt', '成品'), h('dd', `${videoDraft.value.video_count} 个`), h('dt', '画面'), h('dd', videoDraft.value.video_aspect), h('dt', '发布'), h('dd', autoPublish.value.enabled ? '生成后自动发布' : '预览后手动发布')]),
          selectedJob.value ? h('div', [h('strong', selectedJob.value.subject), h('p', { class: 'workspace-note' }, `${statusLabel(selectedJob.value.status)} · ${stageLabel(selectedJob.value.current_stage)} · ${selectedJob.value.progress || 0}%`), selectedJob.value.error ? h('p', { class: 'content-job-error' }, selectedJob.value.error) : null]) : null,
          h('button', { class: 'secondary-action', onClick: () => router.push('/content-records') }, `查看作品库${activeJobCount.value ? `（${activeJobCount.value} 个生成中）` : ''}`),
        ]),
        materialPickerOpen.value ? h(AssetPicker, { assets: assets.value, selectedIds: selectedAssetIds.value, busy: uploading.value, 'onUpdate:selectedIds': (ids: string[]) => selectedAssetIds.value = ids, onImport: uploadAssets, onClose: () => materialPickerOpen.value = false }) : null,
      ])
    }

    function renderAdvancedVideoOptions() {
      return h('details', { class: 'content-fold' }, [
        h('summary', [h('strong', '高级视频参数'), h('span', '转场、速度、字幕和样式')]),
        h('div', { class: 'content-fold-body' }, [
          formSelect('拼接方式', videoDraft.value.video_concat_mode, [['sequential', '按顺序'], ['random', '随机']], value => videoDraft.value.video_concat_mode = value),
          formSelect('转场效果', videoDraft.value.video_transition_mode, [['', '无'], ['Shuffle', '随机'], ['FadeIn', '淡入'], ['FadeOut', '淡出'], ['SlideIn', '滑入'], ['SlideOut', '滑出']], value => videoDraft.value.video_transition_mode = value),
          formNumber('单段秒数', videoDraft.value.video_clip_duration, value => videoDraft.value.video_clip_duration = value, 1, 30),
          formNumber('生成数量', videoDraft.value.video_count, value => videoDraft.value.video_count = value, 1, 10),
          formNumber('语速', videoDraft.value.voice_rate, value => videoDraft.value.voice_rate = value, 0.5, 2, 0.1),
          formNumber('音量', videoDraft.value.voice_volume, value => videoDraft.value.voice_volume = value, 0, 2, 0.1),
          formNumber('背景音乐音量', videoDraft.value.bgm_volume, value => videoDraft.value.bgm_volume = value, 0, 1, 0.05),
          formSelect('字幕位置', videoDraft.value.subtitle_position, [['bottom', '底部'], ['center', '居中'], ['top', '顶部'], ['custom', '自定义']], value => videoDraft.value.subtitle_position = value),
          videoDraft.value.subtitle_position === 'custom' ? formNumber('字幕垂直位置（%）', videoDraft.value.custom_position, value => videoDraft.value.custom_position = value, 0, 100) : null,
          formInput('字幕字体', videoDraft.value.font_name, value => videoDraft.value.font_name = value),
          formNumber('字幕字号', videoDraft.value.font_size, value => videoDraft.value.font_size = value, 12, 160),
          formInput('字幕颜色', videoDraft.value.text_fore_color, value => videoDraft.value.text_fore_color = value, '', 'color'),
          formInput('描边颜色', videoDraft.value.stroke_color, value => videoDraft.value.stroke_color = value, '', 'color'),
          formNumber('描边宽度', videoDraft.value.stroke_width, value => videoDraft.value.stroke_width = value, 0, 10, 0.5),
          h('div', { class: 'toggles field-full' }, [
            formToggle('生成字幕', videoDraft.value.subtitle_enabled, value => videoDraft.value.subtitle_enabled = value),
            formToggle('按文案顺序匹配素材', videoDraft.value.match_materials_to_script, value => videoDraft.value.match_materials_to_script = value),
            formToggle('圆角字幕背景', videoDraft.value.rounded_subtitle_background, value => videoDraft.value.rounded_subtitle_background = value),
          ]),
          formTextarea('文案提示词', videoDraft.value.video_script_prompt, value => videoDraft.value.video_script_prompt = value, 'field-full'),
          formTextarea('系统提示词', videoDraft.value.custom_system_prompt, value => videoDraft.value.custom_system_prompt = value, 'field-full'),
        ]),
      ])
    }

    function renderAutoPublishOptions() {
      const readyAccounts = publishAccounts.value.filter(item => isAccountFeatureReady(item, 'publish'))
      return h('section', { class: 'field-full content-auto-publish' }, [
        h('div', { class: 'content-auto-publish-head' }, [
          h('strong', '生成后自动发布'),
          formToggle('为本次任务启用', Boolean(autoPublish.value.enabled), value => {
            autoPublish.value.enabled = value
            if (value && !autoPublish.value.account_ids.length) autoPublish.value.account_ids = readyAccounts.filter(account => account.default_features.includes('publish')).map(account => account.id)
          }),
        ]),
        autoPublish.value.enabled ? h('div', { class: 'content-auto-publish-body' }, [
          readyAccounts.length ? h('div', { class: 'publish-account-options' }, readyAccounts.map(account => h('label', [
            h('input', { type: 'checkbox', checked: autoPublish.value.account_ids.includes(account.id), onChange: (event: Event) => autoPublish.value.account_ids = togglePublishId(autoPublish.value.account_ids, String(account.id), (event.target as HTMLInputElement).checked) }),
            `${publishPlatformLabel(account.platform)} · ${account.name}`,
          ]))) : h('button', { class: 'secondary-action', onClick: () => router.push({ path: '/accounts', query: { return_to: route.fullPath } }) }, '准备发布账号'),
          formSelect('发布成品', autoPublish.value.output_scope, [['first', '仅首个成品'], ['all', '全部成品']], value => autoPublish.value.output_scope = value),
          formSelect('发布方式', autoPublish.value.publish_strategy, [['immediate', '立即发布'], ['scheduled', '定时发布']], value => autoPublish.value.publish_strategy = value),
          autoPublish.value.publish_strategy === 'scheduled'
            ? h('div', [formInput('发布时间', autoPublish.value.scheduled_at, value => autoPublish.value.scheduled_at = value, '', 'datetime-local'), h('p', { class: 'workspace-note' }, '必须晚于当前时间至少 2 小时；执行时过期会失败，不会改为立即发布。')])
            : null,
          h('p', { class: 'workspace-note' }, `生成成功后将按成品和账号分别建立发布任务；失败后需要先恢复生成，再决定继续发布。`),
        ]) : null,
      ])
    }

    function renderAssetsPage() {
      return h('section', { class: 'pane table-workspace content-assets-page' }, [
        h('div', { class: 'table-library-bar' }, [
          sectionTitle({ title: '素材与我的音色', subtitle: `${assets.value.length} 项托管素材 · 导入一次，多次创作复用`, icon: Collection, tone: 'amber' }),
          h('div', { class: 'table-filters content-asset-filters' }, [
            h('input', { placeholder: '搜索资产名称', value: assetFilter.value.search, onInput: (event: Event) => { assetFilter.value.search = (event.target as HTMLInputElement).value; assetPage.value = 1 } }),
            h('button', { class: 'filter-button', onClick: loadAssets }, '搜索'),
            selectedPublishAssetIds.value.length ? h('button', { class: 'secondary-action', onClick: () => openAssetPublishSettings(selectedPublishAssetIds.value) }, `发布图文（${selectedPublishAssetIds.value.length}）`) : null,
            h('label', { class: ['primary-action content-asset-upload', uploading.value ? 'disabled' : ''] }, [
              h(Plus, { class: 'inline-icon' }),
              uploading.value ? '导入中...' : assetImportLabel(assetSegment.value),
              h('input', { type: 'file', multiple: true, accept: assetAccept(assetSegment.value), disabled: uploading.value, onChange: uploadAssets, hidden: true }),
            ]),
          ]),
        ]),
        h('div', { class: 'content-asset-segments', role: 'tablist', 'aria-label': '内容资产分类' }, ASSET_SEGMENTS.map(([value, label]) => h('button', {
          role: 'tab',
          'aria-selected': assetSegment.value === value,
          class: assetSegment.value === value ? 'active' : '',
          onClick: () => assetSegment.value = value,
        }, [h('span', label), h('small', String(assets.value.filter(asset => assetMatchesSegment(asset, value)).length))]))),
        assetSegment.value === 'voice_reference' ? renderVoiceRecorder() : null,
        assetSegment.value === 'voice_reference' ? renderVoiceProfiles() : null,
        assetSegment.value === 'voice_reference' ? null : visibleAssets.value.length
          ? h('div', { class: 'content-asset-grid' }, assetPagination.value.items.map(renderAssetCard))
          : emptyState({ title: `暂无${assetSegmentLabel(assetSegment.value)}资产`, description: '点击右上角按钮导入，克隆音频也可以直接录制', icon: Collection, tone: 'amber' }),
        assetSegment.value === 'voice_reference' ? null : h(ListPagination, {
          page: assetPagination.value.page,
          pageSize: assetPagination.value.pageSize,
          total: assetPagination.value.total,
          onChange: (payload: PageChange) => {
            assetPage.value = payload.page
            assetPageSize.value = payload.page_size
          },
        }),
      ])
    }

    function renderAssetCard(asset: Dict) {
      return h('article', { class: 'content-asset-card' }, [
        asset.asset_type === 'image' ? h('label', { class: 'content-asset-publish-select' }, [h('input', { type: 'checkbox', checked: selectedPublishAssetIds.value.includes(String(asset.id)), onChange: (event: Event) => selectedPublishAssetIds.value = togglePublishId(selectedPublishAssetIds.value, String(asset.id), (event.target as HTMLInputElement).checked) }), '选择发布']) : null,
        h('div', { class: 'content-asset-preview' }, [renderAssetPreview(asset)]),
        h('div', { class: 'content-asset-info' }, [
          h('strong', { title: asset.name }, String(asset.name || '未命名')),
          h('span', `${assetCategoryLabel(asset)} · ${formatBytes(asset.file_size)}`),
          h('small', asset.duration ? `${Number(asset.duration).toFixed(1)}秒` : asset.width ? `${asset.width}×${asset.height}` : '等待使用时校验'),
        ]),
        h('div', { class: 'task-card-actions' }, [
          asset.asset_type === 'video' ? h('button', { class: 'primary-soft', onClick: () => publishAsset(asset) }, '准备发布') : null,
          asset.asset_type === 'image' ? h('button', { class: 'primary-soft', onClick: () => openAssetPublishSettings([String(asset.id)]) }, '发布设置') : null,
          asset.asset_type === 'audio' && !voiceProfiles.value.some(profile => profile.reference_asset_id === asset.id)
            ? h('button', { class: 'primary-soft', onClick: () => createVoiceProfile(asset) }, '创建音色')
            : null,
          h('button', { class: 'text-icon-button', onClick: () => renameAsset(asset) }, '重命名'),
          h('button', { class: 'text-icon-button danger', onClick: () => deleteAsset(asset) }, [h(Delete, { class: 'inline-icon' }), '删除']),
        ]),
      ])
    }

    function renderVoiceRecorder() {
      return h('section', { class: 'content-voice-recorder' }, [
        h('div', { class: 'content-recorder-head' }, [
          h('div', [h('strong', '录制克隆音频'), h('p', '建议在安静环境中自然朗读20–40秒，尽量与文案保持一致。')]),
          h('button', { class: 'secondary-action', disabled: voiceScriptLoading.value || voiceRecording.value, onClick: generateVoiceReferenceScript }, voiceScriptLoading.value ? '生成中...' : 'AI生成朗读文案'),
        ]),
        h('label', { class: 'content-recorder-script' }, [
          h('span', '朗读文案'),
          h('textarea', {
            rows: 4,
            value: voiceReferenceScript.value,
            disabled: voiceRecording.value,
            placeholder: '点击“AI生成朗读文案”，也可以自行修改后再录音。',
            onInput: (event: Event) => voiceReferenceScript.value = (event.target as HTMLTextAreaElement).value,
          }),
        ]),
        h('div', { class: 'content-recorder-controls' }, [
          h('label', { class: 'content-recorder-name' }, [
            h('span', '录音名称'),
            h('input', { value: voiceRecordingName.value, disabled: voiceRecording.value, onInput: (event: Event) => voiceRecordingName.value = (event.target as HTMLInputElement).value }),
          ]),
          h('div', { class: ['content-recording-status', voiceRecording.value ? 'recording' : ''] }, [
            h('i'),
            h('span', voiceRecording.value ? `录音中 ${formatDuration(voiceRecordingSeconds.value)}` : voiceRecordingBlob.value ? `录音完成 ${formatDuration(voiceRecordingSeconds.value)}` : '等待录音'),
          ]),
          voiceRecording.value
            ? h('button', { class: 'danger-soft', onClick: stopVoiceRecording }, '停止录音')
            : h('button', { class: 'primary-action', disabled: voiceRecordingSaving.value, onClick: startVoiceRecording }, voiceRecordingBlob.value ? '重新录音' : '开始录音'),
        ]),
        voiceRecordingUrl.value ? h('div', { class: 'content-recording-preview' }, [
          h('audio', { src: voiceRecordingUrl.value, controls: true }),
          h('button', { class: 'text-icon-button danger', disabled: voiceRecordingSaving.value, onClick: clearVoiceRecording }, '放弃录音'),
          h('button', { class: 'primary-action', disabled: voiceRecordingSaving.value, onClick: saveVoiceRecording }, voiceRecordingSaving.value ? '保存中...' : '保存并创建音色'),
        ]) : null,
      ])
    }

    function renderVoiceProfiles() {
      const linkedAssetIds = new Set(voiceProfiles.value.map(profile => profile.reference_asset_id))
      const pendingAssets = assets.value.filter(asset => assetMatchesSegment(asset, 'voice_reference') && !linkedAssetIds.has(asset.id))
      const cards = [
        ...voiceProfiles.value.map(profile => h('article', { class: 'content-voice-card' }, [
          h('audio', { src: profile.reference_asset?.preview_url, controls: true, preload: 'metadata' }),
          h('div', { class: 'content-voice-info' }, [
            h('strong', String(profile.name || '未命名音色')),
            h('span', `参考音频 · ${profile.reference_asset?.name || '未命名录音'}`),
            h('small', profile.prompt_text ? '高保真克隆' : '普通克隆'),
          ]),
          h('div', { class: 'task-card-actions' }, [
            h('button', { class: 'text-icon-button', onClick: () => renameVoiceProfile(profile) }, '重命名'),
            h('button', { class: 'text-icon-button danger', onClick: () => deleteVoiceProfile(profile) }, '删除'),
          ]),
        ])),
        ...pendingAssets.map(asset => h('article', { class: 'content-voice-card pending' }, [
          h('audio', { src: asset.preview_url, controls: true, preload: 'metadata' }),
          h('div', { class: 'content-voice-info' }, [
            h('strong', String(asset.name || '未命名录音')),
            h('span', '参考音频 · 尚未创建音色'),
            h('small', '完成创建后即可用于视频配音'),
          ]),
          h('div', { class: 'task-card-actions' }, [
            h('button', { class: 'primary-soft', onClick: () => createVoiceProfile(asset) }, '创建音色'),
            h('button', { class: 'text-icon-button', onClick: () => renameAsset(asset) }, '重命名'),
            h('button', { class: 'text-icon-button danger', onClick: () => deleteAsset(asset) }, '删除'),
          ]),
        ])),
      ]
      const page = paginateItems(cards, voicePage.value, COMPACT_PAGE_SIZE)
      return h('section', { class: 'content-voice-library' }, [
        sectionTitle({ title: '克隆音色', subtitle: `${cards.length} 条音色与参考录音`, icon: MagicStick, tone: 'purple', compact: true }),
        cards.length
          ? h('div', { class: 'content-voice-grid' }, page.items)
          : h('p', { class: 'content-voice-empty' }, '使用上方录音功能，或导入一段参考音频后创建克隆音色。'),
        h(ListPagination, {
          page: page.page,
          pageSize: page.pageSize,
          total: page.total,
          compact: true,
          onChange: (payload: PageChange) => { voicePage.value = payload.page },
        }),
      ])
    }

    function voiceOptionLabel(value: string) {
      if (!value.startsWith('voxcpm2:')) return voiceLabel(value)
      const profile = voiceProfiles.value.find(item => `voxcpm2:${item.id}` === value)
      return profile ? String(profile.name) : '克隆音色'
    }

    function renderRecordsPage() {
      const pageData = libraryTab.value === 'drafts' ? savedDrafts.value : libraryTab.value === 'jobs' ? jobs.value : outputs.value
      return h('section', { class: 'workspace-panel' }, [
        h('div', { class: 'workspace-heading' }, [h('div', [h('h2', '作品库'), h('p', { class: 'workspace-note' }, '草稿、生成过程与每次成品分别保存，重新生成不会覆盖旧成品。')]), h('button', { class: 'primary-action', onClick: newVideoDraft }, '新建视频')]),
        h('nav', { class: 'workspace-tabs', 'aria-label': '作品库分类' }, ([['outputs', '成品'], ['drafts', '草稿'], ['jobs', '生成记录']] as Array<[string, string]>).map(([value, label]) => h('button', { class: libraryTab.value === value ? 'is-active' : '', onClick: () => { const query: Dict = { ...route.query, tab: value }; delete query.job; void router.replace({ path: '/content-records', query }) } }, label))),
        h('div', { class: 'workspace-toolbar', style: { marginBottom: '20px' } }, [
          h('input', { placeholder: '搜索全部作品主题或文件名', value: recordSearch.value, onInput: (event: Event) => recordSearch.value = (event.target as HTMLInputElement).value, onKeyup: (event: KeyboardEvent) => { if (event.key === 'Enter') searchLibrary() } }),
          h('button', { class: 'secondary-action', onClick: searchLibrary }, '搜索'),
          h('button', { class: 'secondary-action', onClick: loadLibrary }, '刷新'),
          libraryTab.value === 'outputs' && selectedOutputIds.value.length ? h('button', { class: 'primary-action', onClick: () => router.push({ path: '/content-publish', query: { output_ids: selectedOutputIds.value.join(',') } }) }, `发布所选 ${selectedOutputIds.value.length} 个成品`) : null,
        ]),
        libraryTab.value === 'jobs' && route.query.job ? renderSelectedJob() : null,
        libraryTab.value === 'drafts' ? h('div', { class: 'content-work-list' }, savedDrafts.value.items.map((draft: Dict) => h('article', { class: 'content-work-row' }, [
          h('div', [h('h3', draft.payload?.subject || draft.payload?.params?.video_subject || '未命名草稿'), h('p', { class: 'workspace-note' }, `更新于 ${draft.updated_at || '—'} · ${draft.payload?.asset_ids?.length || 0} 项素材`)]),
          h('div', { class: 'content-work-actions' }, [draft.payload?.return_context?.query?.job ? h('button', { class: 'primary-soft', onClick: () => router.push({ path: '/content-records', query: draft.payload.return_context.query }) }, '查看已提交生成任务') : h('button', { class: 'primary-soft', onClick: () => router.push({ path: '/content-create', query: { draft_id: draft.key } }) }, '继续编辑'), h('button', { class: 'danger-soft', onClick: () => removeSavedDraft(draft.key) }, '删除草稿')]),
        ]))) : libraryTab.value === 'jobs' ? renderJobList(jobs.value.items || []) : h('div', { class: 'content-output-grid' }, (outputs.value.items || []).map((output: Dict) => renderGeneratedCard({ output, job: { id: output.video_job_id, subject: output.subject, attempt: output.attempt, finished_at: output.created_at } }))),
        !pageData.items?.length ? emptyState({ title: libraryTab.value === 'drafts' ? '暂无创作草稿' : libraryTab.value === 'jobs' ? '暂无生成记录' : '暂无视频成品', description: recordSearch.value ? '没有符合条件的记录，请调整搜索。' : '从新建视频开始准备内容。', icon: VideoPlay }) : null,
        h(ListPagination, { page: pageData.page || 1, pageSize: pageData.page_size || 20, total: pageData.total || 0, onChange: (value: PageChange) => { pageData.page = value.page; pageData.page_size = value.page_size; void loadLibrary() } }),
        libraryTab.value === 'jobs' && !route.query.job ? renderSelectedJob() : null,
      ])
    }

    function renderSelectedJob() {
      return selectedJob.value ? h('section', { class: 'content-job-detail' }, [
          h('div', { class: 'workspace-heading' }, [h('div', [h('h3', selectedJob.value.subject), h('p', { class: 'workspace-note' }, `第 ${selectedJob.value.attempt || 0} 次执行 · ${statusLabel(selectedJob.value.status)} · ${stageLabel(selectedJob.value.current_stage)}`)]), h('button', { class: 'secondary-action', onClick: () => editJobAsDraft(selectedJob.value!) }, '修改后重新生成')]),
          selectedJob.value.error ? h('p', { class: 'content-job-error' }, selectedJob.value.error) : null,
          h('div', { class: 'content-output-grid' }, (selectedJob.value.outputs || []).map((output: Dict) => renderGeneratedCard({ job: selectedJob.value, output }))),
        ]) : null
    }

    function searchLibrary() {
      outputs.value.page = 1; savedDrafts.value.page = 1; jobs.value.page = 1
      selectedJob.value = null
      void loadLibrary()
    }

    function renderGeneratedCard(item: Dict) {
      const job = item.job as Dict
      const output = item.output as Dict
      const outputTasks = output.publish_tasks || []
      const outputId = String(output.output_id || output.id)
      return h('article', { class: 'content-output-card' }, [
        h('video', { src: output.url, controls: true, preload: 'metadata' }),
        h('div', { class: 'content-output-copy' }, [
          h('strong', { title: output.subject }, String(output.subject || '未命名视频')),
          h('span', `${output.name || '生成视频'} · 第${output.attempt || 1}次生成`),
          h('time', `生成于 ${output.created_at || '-'}`),
          h('label', { class: 'content-output-select' }, [h('input', { type: 'checkbox', checked: selectedOutputIds.value.includes(outputId), onChange: (event: Event) => selectedOutputIds.value = togglePublishId(selectedOutputIds.value, outputId, (event.target as HTMLInputElement).checked) }), '选择发布']),
          outputTasks.length ? h('div', { class: 'content-publish-badges' }, outputTasks.map((task: Dict) => h('span', { class: `status-pill status-${task.status}` }, `${publishPlatformLabel(task.platform)} ${publishTaskStatusLabel(task.status)}`))) : h('small', '尚未发布'),
        ]),
        h('div', { class: 'content-output-copy' }, [
          h('label', [h('span', '人工标记'), h('select', {
            value: output.upload_status || 'not_uploaded',
            onChange: (event: Event) => updateOutputStatus(job, output, (event.target as HTMLSelectElement).value),
          }, [h('option', { value: 'not_uploaded' }, '未上传'), h('option', { value: 'uploaded' }, '已上传')])]),
          h('div', { class: 'content-publish-split' }, [
            h('button', { class: 'primary-soft', onClick: () => openPublishSettings(output) }, '准备发布'),
          ]),
        ]),
        h('div', { class: 'content-output-copy workspace-toolbar' }, [
          h('a', { class: 'text-icon-button', href: output.url, download: output.name }, '下载'),
          h('button', { class: 'text-icon-button', onClick: () => router.push({ path: '/content-records', query: { job: job.id } }) }, '查看生成记录'),
        ]),
      ])
    }

    function renderJobList(rows: Dict[]) {
      if (!rows.length) return emptyState({ title: '暂无视频任务', description: '在视频创作页面创建第一条任务', icon: Tickets })
      return h('div', { class: 'content-job-list' }, rows.map(job => h('article', {
        class: ['content-job-card', selectedJob.value?.id === job.id ? 'selected' : ''],
      }, [
        h('button', { class: 'content-job-main', onClick: () => selectedJob.value = job }, [
          h('div', { class: 'content-job-preview' }, [
            job.outputs?.[0]?.url
              ? h('video', { src: job.outputs[0].url, muted: true, preload: 'metadata' })
              : h(VideoPlay, { class: 'content-job-placeholder' }),
          ]),
          h('div', { class: 'content-job-copy' }, [
            h('div', { class: 'content-job-head' }, [h('strong', String(job.subject || '未命名视频')), h('span', { class: `status-pill status-${job.status}` }, statusLabel(job.status))]),
            h('small', `${stageLabel(job.current_stage)} · ${job.progress || 0}% · 第${job.attempt || 0}次`),
            h('div', { class: 'content-job-progress' }, [h('span', { style: { width: `${Number(job.progress || 0)}%` } })]),
            job.error ? h('p', { class: 'content-job-error' }, String(job.error)) : null,
          ]),
        ]),
        renderJobActions(job),
      ])))
    }

    function renderJobActions(job: Dict) {
      return h('div', { class: 'content-job-card-actions' }, [
        h('button', { class: 'text-icon-button', onClick: () => editJobAsDraft(job) }, '修改后生成'),
        h('button', { class: 'text-icon-button', onClick: () => renameJob(job) }, '重命名'),
        ['queued', 'running'].includes(String(job.status)) ? h('button', { class: 'danger-soft', onClick: () => cancelJob(String(job.id)) }, '取消') : null,
        ['failed', 'cancelled', 'interrupted'].includes(String(job.status)) ? h('button', { class: 'primary-soft', onClick: () => retryJob(String(job.id)) }, '重试原配置') : null,
        job.outputs?.length ? h('button', { class: 'primary-soft', onClick: () => router.push({ path: '/content-records', query: { job: job.id } }) }, '选择成品发布') : null,
        !['queued', 'running'].includes(String(job.status)) ? h('button', { class: 'text-icon-button danger', onClick: () => archiveJob(String(job.id)) }, '归档记录') : null,
      ])
    }

    function renderSettingsPage() {
      return h('div', { class: 'content-settings-layout' }, [
        h('section', { class: 'workspace-panel content-settings-pane' }, [
          sectionTitle({ title: '内容设置', subtitle: settingsDirty.value ? '有未保存修改，自动刷新不会覆盖当前草稿' : '模型连接、素材、配音与运行环境', icon: Setting, tone: settingsDirty.value ? 'amber' : 'teal', aside: h('button', { class: 'primary-action', disabled: settingsSaving.value, onClick: saveSettings }, settingsSaving.value ? '保存中...' : '保存设置') }),
          h('section', { class: 'content-model-connection' }, [
            h('h3', '内容模型连接'),
            formSelect('模型连接方式', settingsDraft.value.model_connection_mode || 'shared', [['shared', '复用全局模型连接'], ['custom', '为内容单独配置']], value => updateSetting('model_connection_mode', value)),
            settingsDraft.value.model_connection_mode !== 'custom' ? h('div', [
              h('p', { class: 'workspace-note' }, '文案和发布文案使用全局模型连接，无需重复填写密钥。素材来源与配音供应商仍各自配置。'),
              settings.value.model_connection?.mode === 'shared' ? h('p', { class: 'workspace-note' }, `${settings.value.model_connection.model || '模型未配置'} · ${settings.value.model_connection.base_url || '服务地址未配置'} · ${settings.value.model_connection.api_key_configured ? '密钥已配置' : '密钥未配置'}`) : null,
              h('button', { class: 'secondary-action', onClick: () => router.push('/settings') }, '管理全局模型连接'),
            ]) : h('p', { class: 'workspace-note' }, '此处只配置所选文案供应商。Gemini、MiMo 的密钥和连接与配音共用，位于语音配置。'),
          ]),
          ...settingGroups.filter(group => group.title !== 'AI供应商' || settingsDraft.value.model_connection_mode === 'custom').map(group => {
            const provider = String(settingsDraft.value.app?.llm_provider || 'openai')
            const fields = group.title === 'AI供应商' ? group.fields.filter(field => field.path === 'app.llm_provider' || field.path.startsWith('app.' + provider + '_') && !['app.gemini_api_key', 'app.mimo_api_key', 'app.mimo_base_url'].includes(field.path)) : group.fields
            return h('details', { class: 'settings-fold', open: group.title === '素材与基础处理' || group.title === 'AI供应商' }, [
            h('summary', [h('strong', group.title), h('span', `${fields.length} 项配置`)]),
            h('div', { class: 'settings-fold-body form-grid content-settings-grid' }, fields.map(renderSettingField)),
          ]) }),
        ]),
        h('aside', { class: 'workspace-panel content-env-pane' }, [
          sectionTitle({ title: '内容环境', subtitle: environment.value.ok ? '可以生成视频' : '存在缺失项', icon: Setting, tone: environment.value.ok ? 'green' : 'amber', aside: h('button', { type: 'button', class: 'icon-refresh', title: '刷新内容环境', 'aria-label': '刷新内容环境', onClick: loadEnvironment }, [h(Refresh)]) }),
          renderEnvironment(),
        ]),
      ])
    }

    function renderSettingField(field: SettingField) {
      const value = getPath(settingsDraft.value, field.path)
      if (field.type === 'boolean') return formSelect(field.label, String(Boolean(value)), [['true', '开启'], ['false', '关闭']], selected => updateSetting(field.path, selected === 'true'))
      if (field.options) return formSelect(field.label, String(value ?? ''), field.options, selected => updateSetting(field.path, selected))
      if (field.type === 'password' || field.path.endsWith('_api_keys')) {
        const configured = Boolean(getPath(settingsDraft.value, field.path + '_configured'))
        const clearing = clearSecrets.value.includes(field.path)
        return h('div', { class: 'content-secret-setting' }, [
          field.type === 'list' ? formTextarea(field.label, Array.isArray(value) ? value.join('\n') : '', selected => updateSetting(field.path, selected.split(/[\n,]+/).map(item => item.trim()).filter(Boolean)))
            : formInput(field.label, String(value || ''), selected => updateSetting(field.path, selected), '', 'password'),
          h('small', { class: 'workspace-note' }, clearing ? '保存设置时清除已存凭据' : configured ? '已配置；留空保留，填写新值替换。' : '尚未配置'),
          configured && !clearing ? h('button', { class: 'text-icon-button danger', onClick: () => clearSavedSecret(field) }, '清除已保存密钥') : null,
        ])
      }
      if (field.type === 'list') return formTextarea(field.label, Array.isArray(value) ? value.join('\n') : String(value || ''), selected => updateSetting(field.path, selected.split(/[\n,]+/).map(item => item.trim()).filter(Boolean)))
      if (field.type === 'textarea') return formTextarea(field.label, String(value || ''), selected => updateSetting(field.path, selected), 'field-full')
      if (field.type === 'number') return formNumber(field.label, Number(value || 0), selected => updateSetting(field.path, selected))
      return formInput(field.label, String(value ?? ''), selected => updateSetting(field.path, selected))
    }

    function updateSetting(path: string, value: unknown) {
      setPath(settingsDraft.value, path, value)
      if (Array.isArray(value) ? value.length : value) clearSecrets.value = clearSecrets.value.filter(item => item !== path)
      settingsDirty.value = true
      settingsMutationSeq += 1
    }

    async function clearSavedSecret(field: SettingField) {
      try {
        await ElMessageBox.confirm(`保存设置时清除“${field.label}”？使用此凭据的功能将需要重新配置。`, '清除内容凭据', { type: 'warning' })
        updateSetting(field.path, field.type === 'list' ? [] : '')
        clearSecrets.value = [...new Set([...clearSecrets.value, field.path])]
      } catch (error: any) { if (!isUserCancel(error)) ElMessage.error('无法清除内容凭据') }
    }

    function renderEnvironment() {
      const deps = environment.value.dependencies || {}
      return h('div', { class: 'content-env-list' }, [
        environmentRow('FFmpeg', environment.value.ffmpeg?.ok, environment.value.ffmpeg?.path || '未找到'),
        environmentRow('字幕字体', environment.value.fonts?.ok, `${environment.value.fonts?.count || 0} 个`),
        environmentRow('Whisper模型', environment.value.whisper?.downloaded, environment.value.whisper?.downloaded ? '已下载' : '首次使用时自动下载'),
        renderVoiceRuntimeEnvironment(),
        ...Object.entries(deps).map(([name, ok]) => environmentRow(name, Boolean(ok), ok ? '已安装' : '缺失')),
        h('div', { class: 'content-disk-info' }, `可用磁盘：${formatBytes(environment.value.disk?.free || 0)}`),
      ])
    }

    function environmentRow(label: string, ok: boolean, detail: string) {
      return h('div', { class: ['content-env-row', ok ? 'ok' : 'warn'] }, [h('strong', label), h('span', detail)])
    }

    function renderVoiceRuntimeEnvironment() {
      const runtime = voiceRuntime.value
      const job = voiceRuntimeJob.value
      const progress = Number(job.result?.percent || 0)
      const development = runtime.mode === 'development'
      const detail = development
        ? `开发环境 VoxCPM2 ${runtime.version || ''} 已就绪${runtime.downloaded ? '，模型已下载' : '，模型将在首次使用时下载'}`
        : runtime.installed
        ? `组件 ${runtime.version || ''} 已安装${runtime.downloaded ? '，模型已就绪' : '，模型将在首次使用时下载'}`
        : String(job.error || job.result?.message || '可按需下载安装，不占用主程序下载体积')
      const action = development
        ? null
        : voiceRuntimeInstalling.value
        ? h('button', { type: 'button', class: 'ghost-button', onClick: cancelVoiceRuntimeInstall }, '取消')
        : h('button', { type: 'button', class: runtime.installed ? 'ghost-button' : 'primary-action content-runtime-install', onClick: installVoiceRuntime }, runtime.installed ? '检查更新' : '安装')
      return h('div', { class: ['content-env-row', 'content-runtime-row', runtime.installed ? 'ok' : 'warn'] }, [
        h('div', { class: 'content-runtime-summary' }, [h('strong', '音色克隆'), h('span', detail)]),
        action,
        voiceRuntimeInstalling.value
          ? h('div', { class: 'content-job-progress content-runtime-progress' }, [h('span', { style: { width: `${Math.max(2, progress)}%` } })])
          : null,
      ])
    }

    function renderAssetPreview(asset: Dict) {
      if (asset.asset_type === 'image') return h('img', { src: asset.thumbnail_url || asset.preview_url, alt: asset.name })
      if (asset.asset_type === 'video') return h('video', { src: asset.preview_url, controls: true, preload: 'metadata' })
      return h('audio', { src: asset.preview_url, controls: true, preload: 'metadata' })
    }

    function renderAssetThumb(asset: Dict) {
      if (asset.thumbnail_url) return h('img', { src: asset.thumbnail_url, alt: '' })
      return h(asset.asset_type === 'image' ? Collection : VideoPlay, { class: 'content-asset-placeholder' })
    }

    return () => {
      return h('div', { class: 'workspace-page content-product' }, [view.value === 'content-create' ? renderCreatePage() : view.value === 'content-assets' ? renderAssetsPage() : view.value === 'content-records' ? renderRecordsPage() : renderSettingsPage()])
    }
  },
})

function formInput(label: string, value: unknown, update: (value: string) => unknown, className = '', type = 'text') {
  return h('label', { class: ['form-field', className] }, [
    h('span', label),
    h('input', { type, value: String(value ?? ''), onInput: (event: Event) => update((event.target as HTMLInputElement).value) }),
  ])
}

function formTextarea(label: string, value: unknown, update: (value: string) => unknown, className = '') {
  return h('label', { class: ['form-field', className] }, [
    h('span', label),
    h('textarea', { value: String(value ?? ''), rows: 4, onInput: (event: Event) => update((event.target as HTMLTextAreaElement).value) }),
  ])
}

function formSelect(label: string, value: unknown, options: Array<[string, string]>, update: (value: string) => unknown) {
  return h('label', { class: 'form-field' }, [
    h('span', label),
    h('select', { value: String(value ?? ''), onChange: (event: Event) => update((event.target as HTMLSelectElement).value) }, options.map(([optionValue, text]) => h('option', { value: optionValue }, text))),
  ])
}

function formNumber(label: string, value: unknown, update: (value: number) => unknown, min?: number, max?: number, step = 1) {
  return h('label', { class: 'form-field' }, [
    h('span', label),
    h('input', { type: 'number', value: Number(value || 0), min, max, step, onInput: (event: Event) => update(Number((event.target as HTMLInputElement).value)) }),
  ])
}

function formToggle(label: string, value: boolean, update: (value: boolean) => unknown) {
  return h('label', { class: 'toggle-item' }, [
    h('input', { type: 'checkbox', checked: Boolean(value), onChange: (event: Event) => update((event.target as HTMLInputElement).checked) }),
    h('span', label),
  ])
}

function getPath(target: Dict, path: string) {
  return path.split('.').reduce((value: any, key) => value?.[key], target)
}

function setPath(target: Dict, path: string, value: unknown) {
  const parts = path.split('.')
  let current: Dict = target
  parts.slice(0, -1).forEach(key => {
    if (!current[key] || typeof current[key] !== 'object') current[key] = {}
    current = current[key]
  })
  current[parts[parts.length - 1]] = value
}

export function assetMatchesSegment(asset: Dict, segment: string) {
  if (segment === 'all') return true
  if (segment === 'video' || segment === 'image') return asset.asset_type === segment
  if (segment === 'voice_reference') return asset.asset_type === 'audio' && asset.purpose === 'voice_reference'
  return asset.asset_type === 'audio' && asset.purpose === 'background_music'
}

export function filterMaterialAssets(assets: Dict[], kind: string) {
  return kind === 'all' ? assets : assets.filter(asset => asset.asset_type === kind)
}

export function paginateMaterialAssets(assets: Dict[], page: number, pageSize: number) {
  return paginateItems(assets, page, pageSize).items
}

function fileNameWithExtension(name: string, originalName: string) {
  const extension = String(originalName || '').match(/\.[^.]+$/)?.[0] || ''
  return `${String(name || '').replace(/\.[^.]+$/, '')}${extension}`
}

function assetCategoryLabel(asset: Dict) {
  if (asset.asset_type === 'audio') return asset.purpose === 'voice_reference' ? '克隆音频' : '背景音乐'
  return asset.asset_type === 'video' ? '视频' : asset.asset_type === 'image' ? '图片' : '文件'
}

function assetSegmentLabel(segment: string) {
  return ASSET_SEGMENTS.find(([value]) => value === segment)?.[1] || '内容'
}

function assetImportLabel(segment: string) {
  return segment === 'all' ? '导入内容资产' : `导入${assetSegmentLabel(segment)}`
}

function assetAccept(segment: string) {
  if (segment === 'video') return 'video/*'
  if (segment === 'image') return 'image/*'
  if (['background_music', 'voice_reference'].includes(segment)) return 'audio/*'
  return 'video/*,image/*,audio/*'
}

function recordingExtension(mimeType: string) {
  if (mimeType.includes('mp4')) return 'm4a'
  if (mimeType.includes('ogg')) return 'ogg'
  return 'webm'
}

function formatDuration(seconds: number) {
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

function statusLabel(value: unknown) {
  return ({ queued: '排队中', running: '生成中', succeeded: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' } as Record<string, string>)[String(value || '')] || String(value || '-')
}

function stageLabel(value: unknown) {
  return ({ queued: '等待执行', preparing: '准备环境', script: '生成文案', terms: '生成素材词', audio: '生成配音', subtitle: '生成字幕', materials: '准备素材', rendering: '合成视频', completed: '生成完成', failed: '生成失败', cancelled: '已取消', interrupted: '已中断' } as Record<string, string>)[String(value || '')] || String(value || '-')
}

function publishPlatformLabel(value: unknown) {
  return ({ dy: '抖音', ks: '快手', xhs: '小红书' } as Record<string, string>)[String(value || '')] || String(value || '')
}

function publishTaskStatusLabel(value: unknown) {
  return ({ waiting_media: '等待成品', queued: '待发布', running: '发布中', succeeded: '已发布', failed: '失败', review_required: '待确认', cancelled: '已取消' } as Record<string, string>)[String(value || '')] || String(value || '')
}

function togglePublishId(values: string[], id: string, checked: boolean) {
  return checked ? [...new Set([...values, id])] : values.filter(value => value !== id)
}

function voiceLabel(value: string) {
  return value.replace('-Female', ' 女声').replace('-Male', ' 男声').replace('Neural', '')
}

function inferVoiceProvider(value: string) {
  const prefix = value.split(':')[0]
  if (['voxcpm2', 'siliconflow', 'gemini', 'mimo', 'elevenlabs', 'chatterbox'].includes(prefix)) return prefix
  return value.replace(/-(Female|Male)$/, '').endsWith('-V2') ? 'azure-v2' : 'edge'
}

function formatBytes(value: unknown) {
  const bytes = Number(value || 0)
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${bytes} B`
}

function isUserCancel(error: unknown) {
  return error === 'cancel' || error === 'close'
}
