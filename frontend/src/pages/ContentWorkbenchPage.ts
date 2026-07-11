import { computed, defineComponent, h, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
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
import type { Dict } from '../shared/types'
import { SplitPane } from '../components/ui/SplitPane'
import { emptyState, sectionTitle } from '../components/ui/Workbench'


const MASKED_SECRET = '********'
const ASSET_SEGMENTS = [
  ['all', '全部'],
  ['video', '视频'],
  ['image', '图片'],
  ['background_music', '背景音乐'],
  ['voice_reference', '克隆音频'],
] as const

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
    title: 'TwelveLabs与自动发布',
    fields: [
      { path: 'app.twelvelabs_api_keys', label: 'TwelveLabs API Keys', type: 'list' },
      { path: 'app.twelvelabs_rerank_terms', label: '启用素材词语义重排', type: 'boolean' },
      { path: 'app.upload_post_enabled', label: '启用Upload-Post', type: 'boolean' },
      { path: 'app.upload_post_api_key', label: 'Upload-Post API Key', type: 'password' },
      { path: 'app.upload_post_username', label: 'Upload-Post用户名' },
      { path: 'app.upload_post_platforms', label: '发布平台', type: 'list' },
      { path: 'app.upload_post_auto_upload', label: '生成后自动发布', type: 'boolean' },
      { path: 'app.upload_post_youtube_privacy_status', label: 'YouTube可见性', options: [['public', '公开'], ['unlisted', '不公开列出'], ['private', '私密']] },
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
    const jobs = ref<Dict>({ items: [], total: 0, page: 1, page_size: 100 })
    const selectedJob = ref<Dict | null>(null)
    const settings = ref<Dict>({})
    const settingsDraft = ref<Dict>({})
    const environment = ref<Dict>({})
    const videoDraft = ref<Dict>(defaultVideoDraft())
    const selectedAssetIds = ref<string[]>([])
    const audioAssetId = ref('')
    const bgmAssetId = ref('')
    const assetFilter = ref({ search: '' })
    const assetSegment = ref('all')
    const recordSearch = ref('')
    const voiceProvider = ref('edge')
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
    let refreshTimer = 0
    let voiceRecordingTimer = 0
    let mediaRecorder: MediaRecorder | null = null
    let mediaStream: MediaStream | null = null
    let mediaChunks: Blob[] = []

    const materialAssets = computed(() => assets.value.filter(item => ['video', 'image'].includes(String(item.asset_type))))
    const audioAssets = computed(() => assets.value.filter(item => String(item.asset_type) === 'audio'))
    const visibleAssets = computed(() => assets.value.filter(asset => assetMatchesSegment(asset, assetSegment.value)))
    const selectedMaterials = computed(() => selectedAssetIds.value
      .map(id => materialAssets.value.find(item => item.id === id))
      .filter(Boolean) as Dict[])
    const activeJobs = computed(() => (jobs.value.items || []).filter((job: Dict) => ['queued', 'running'].includes(String(job.status))))
    const generatedItems = computed(() => {
      const query = recordSearch.value.trim().toLowerCase()
      return (jobs.value.items || []).flatMap((job: Dict) => (job.outputs || []).map((output: Dict) => ({ job, output })))
        .filter(({ job, output }: Dict) => !query || `${job.subject || ''} ${output.name || ''}`.toLowerCase().includes(query))
    })

    onMounted(() => {
      void loadPage()
      refreshTimer = window.setInterval(() => {
        if (activeJobs.value.length && ['content-create', 'content-records'].includes(view.value)) void loadJobs(false)
      }, 3000)
    })
    onUnmounted(() => {
      window.clearInterval(refreshTimer)
      window.clearInterval(voiceRecordingTimer)
      if (mediaRecorder?.state === 'recording') mediaRecorder.stop()
      mediaStream?.getTracks().forEach(track => track.stop())
      if (voiceRecordingUrl.value) URL.revokeObjectURL(voiceRecordingUrl.value)
    })
    watch(view, () => void loadPage())
    watch(() => props.refreshSeq, () => void loadPage())

    async function loadPage() {
      if (view.value === 'content-create') await Promise.all([loadAssets(), loadVoiceProfiles(), loadJobs(false), loadVoices()])
      else if (view.value === 'content-assets') await Promise.all([loadAssets(), loadVoiceProfiles()])
      else if (view.value === 'content-records') await loadJobs(true)
      else await Promise.all([loadSettings(), loadEnvironment()])
    }

    async function loadAssets() {
      const { data } = await api.get('/content/assets', { params: { search: assetFilter.value.search } })
      assets.value = data
      selectedAssetIds.value = selectedAssetIds.value.filter(id => data.some((item: Dict) => item.id === id))
    }

    async function loadVoiceProfiles() {
      const { data } = await api.get('/content/voice-profiles')
      voiceProfiles.value = data
    }

    async function loadJobs(selectFirst = false) {
      const { data } = await api.get('/content/video-jobs', { params: { page: jobs.value.page || 1, page_size: jobs.value.page_size || 20 } })
      jobs.value = data
      if (selectedJob.value) {
        const found = data.items.find((item: Dict) => item.id === selectedJob.value?.id)
        if (found) selectedJob.value = found
      }
      if (selectFirst && !selectedJob.value && data.items.length) selectedJob.value = data.items[0]
    }

    async function loadSettings() {
      const { data } = await api.get('/content/settings')
      settings.value = data
      settingsDraft.value = JSON.parse(JSON.stringify(data))
    }

    async function loadEnvironment() {
      const { data } = await api.get('/content/environment-check')
      environment.value = data
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
        await api.patch(`/content/voice-profiles/${profile.id}`, { name: result.value })
        await loadVoiceProfiles()
      } catch (error: any) {
        if (isUserCancel(error)) return
        ElMessage.error(error?.response?.data?.detail || '音色修改失败')
      }
    }

    async function deleteVoiceProfile(profile: Dict) {
      try {
        await ElMessageBox.confirm(`确认删除音色“${profile.name}”？参考音频资产会继续保留。`, '删除克隆音色', { type: 'warning' })
        await api.delete(`/content/voice-profiles/${profile.id}`)
        await loadVoiceProfiles()
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
      const draft = JSON.parse(JSON.stringify(videoDraft.value))
      if (!String(draft.video_subject || '').trim()) return ElMessage.warning('请填写视频主题')
      if (draft.video_source === 'local' && !selectedAssetIds.value.length) return ElMessage.warning('本地素材模式至少选择一项内容资产')
      if (voiceProvider.value === 'voxcpm2' && !audioAssetId.value && !String(draft.voice_name || '').trim()) return ElMessage.warning('请先选择一个克隆音色')
      draft.video_transition_mode = draft.video_transition_mode || null
      loading.value = true
      try {
        const { data } = await api.post('/content/video-jobs', {
          params: draft,
          asset_ids: selectedAssetIds.value,
          audio_asset_id: audioAssetId.value,
          bgm_asset_id: bgmAssetId.value,
        })
        ElMessage.success('视频任务已加入生成队列')
        selectedJob.value = data
        await loadJobs(false)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '视频任务创建失败')
      } finally {
        loading.value = false
      }
    }

    function clearVideoForm() {
      videoDraft.value = defaultVideoDraft()
      selectedAssetIds.value = []
      audioAssetId.value = ''
      bgmAssetId.value = ''
      voiceProvider.value = 'edge'
      void loadVoices()
      ElMessage.success('视频创作内容已清空')
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

    async function publishJob(id: string) {
      try {
        const { data } = await api.post(`/content/video-jobs/${id}/publish`)
        const failed = (data.results || []).filter((item: Dict) => !item.success).length
        if (failed) ElMessage.warning(`发布完成，${failed} 项失败`)
        else ElMessage.success('视频发布成功')
        await loadJobs(false)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '视频发布失败')
      }
    }

    async function uploadOutput(job: Dict, output: Dict) {
      try {
        const { data } = await api.post(`/content/video-jobs/${job.id}/publish`, null, { params: { output_name: output.name } })
        const failed = (data.results || []).some((item: Dict) => !item.success)
        if (failed) ElMessage.warning('视频上传失败，请查看发布配置或服务返回信息')
        else ElMessage.success('视频上传成功')
        await loadJobs(false)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '视频上传失败')
      }
    }

    async function updateOutputStatus(job: Dict, output: Dict, uploadStatus: string) {
      try {
        await api.patch(`/content/video-jobs/${job.id}/output-status`, { output_name: output.name, upload_status: uploadStatus })
        await loadJobs(false)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '上传状态修改失败')
      }
    }

    async function saveSettings() {
      try {
        const { data } = await api.put('/content/settings', { values: settingsDraft.value })
        settings.value = data
        settingsDraft.value = JSON.parse(JSON.stringify(data))
        ElMessage.success('内容设置已保存')
        await loadEnvironment()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '内容设置保存失败')
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
      return h(SplitPane, { class: 'content-card-split', storageKey: 'content-create', side: 'right', defaultSideWidth: 430, minSideWidth: 360 }, {
        default: () => h('section', { class: 'pane content-pane content-create-pane' }, [
          sectionTitle({ title: '视频创作', subtitle: '主题、素材、配音和字幕一次完成', icon: MagicStick, tone: 'purple' }),
          h('div', { class: 'form-grid content-video-form' }, [
            formInput('视频主题', videoDraft.value.video_subject, value => videoDraft.value.video_subject = value, 'field-full'),
            formSelect('素材来源', videoDraft.value.video_source, [['local', '内容资产'], ['pexels', 'Pexels'], ['pixabay', 'Pixabay'], ['coverr', 'Coverr']], value => videoDraft.value.video_source = value),
            formSelect('画面比例', videoDraft.value.video_aspect, [['9:16', '竖屏 9:16'], ['16:9', '横屏 16:9'], ['1:1', '方形 1:1']], value => videoDraft.value.video_aspect = value),
            formTextarea('视频文案', videoDraft.value.video_script, value => videoDraft.value.video_script = value, 'field-full'),
            h('div', { class: 'task-card-actions field-full content-ai-actions' }, [
              h('button', { class: 'secondary-action', disabled: loading.value, onClick: generateScript }, 'AI生成文案'),
              h('button', { class: 'secondary-action', disabled: loading.value, onClick: generateTerms }, 'AI生成素材词'),
            ]),
            formTextarea('素材关键词', videoDraft.value.video_terms, value => videoDraft.value.video_terms = value, 'field-full'),
            videoDraft.value.video_source === 'local' ? renderMaterialSelector() : null,
            formSelect('音色供应商', voiceProvider.value, [['edge', 'Edge TTS'], ['voxcpm2', 'VoxCPM2 音色克隆'], ['azure-v2', 'Azure Speech'], ['siliconflow', '硅基流动'], ['gemini', 'Gemini TTS'], ['mimo', 'MiMo TTS'], ['elevenlabs', 'ElevenLabs'], ['chatterbox', 'Chatterbox']], value => { voiceProvider.value = value; void loadVoices() }),
            voices.value.length || voiceProvider.value === 'voxcpm2'
              ? formSelect('配音音色', videoDraft.value.voice_name, voices.value.length ? voices.value.map(value => [value, voiceOptionLabel(value)]) : [['', '请先在内容资产中创建克隆音色']], value => videoDraft.value.voice_name = value)
              : formInput('配音音色', videoDraft.value.voice_name, value => videoDraft.value.voice_name = value),
            formSelect('自定义配音', audioAssetId.value, [['', '使用TTS'], ...audioAssets.value.map((item): [string, string] => [String(item.id), String(item.name)])], value => audioAssetId.value = value),
            formSelect('背景音乐', bgmAssetId.value, [['', '不使用背景音乐'], ...audioAssets.value.map((item): [string, string] => [String(item.id), String(item.name)])], value => bgmAssetId.value = value),
            renderAdvancedVideoOptions(),
          ]),
          h('div', { class: 'task-card-actions content-create-actions' }, [
            h('button', { class: 'secondary-action', disabled: loading.value, onClick: clearVideoForm }, '清空'),
            h('button', { class: 'primary-action', disabled: loading.value, onClick: createVideo }, loading.value ? '提交中...' : '开始生成视频'),
          ]),
        ]),
        side: () => h('aside', { class: 'pane side-pane content-create-side' }, [
          sectionTitle({ title: '生成队列', subtitle: `${activeJobs.value.length} 个进行中`, icon: Tickets, tone: 'blue', aside: h('button', { class: 'icon-refresh', onClick: () => loadJobs(false) }, [h(Refresh)]) }),
          renderJobList((jobs.value.items || []).slice(0, 10)),
        ]),
      })
    }

    function renderMaterialSelector() {
      return h('div', { class: 'form-field field-full' }, [
        h('span', `本地素材（已选 ${selectedAssetIds.value.length} 项）`),
        materialAssets.value.length
          ? h('div', { class: 'content-material-picker' }, materialAssets.value.map(asset => h('button', {
              class: ['content-material-option', selectedAssetIds.value.includes(String(asset.id)) ? 'selected' : ''],
              onClick: () => toggleMaterial(String(asset.id)),
            }, [renderAssetThumb(asset), h('span', String(asset.name || '未命名'))])))
          : emptyState({ title: '还没有视频或图片资产', description: '请先到内容资产页面导入', icon: Collection }),
        selectedMaterials.value.length ? h('div', { class: 'content-material-order' }, selectedMaterials.value.map((asset, index) => h('div', {
          class: 'content-material-order-item',
          draggable: true,
          onDragstart: () => draggedAssetId.value = String(asset.id),
          onDragover: (event: DragEvent) => event.preventDefault(),
          onDrop: () => { reorderMaterial(draggedAssetId.value, String(asset.id)); draggedAssetId.value = '' },
        }, [h('strong', String(index + 1)), h('span', String(asset.name || '未命名')), h('small', '拖动排序')]))) : null,
      ])
    }

    function renderAdvancedVideoOptions() {
      return h('details', { class: 'settings-fold field-full content-advanced-settings' }, [
        h('summary', [h('strong', '高级视频参数'), h('span', '转场、速度、字幕和样式')]),
        h('div', { class: 'settings-fold-body form-grid content-advanced-grid' }, [
          formSelect('拼接方式', videoDraft.value.video_concat_mode, [['sequential', '按顺序'], ['random', '随机']], value => videoDraft.value.video_concat_mode = value),
          formSelect('转场效果', videoDraft.value.video_transition_mode, [['', '无'], ['Shuffle', '随机'], ['FadeIn', '淡入'], ['FadeOut', '淡出'], ['SlideIn', '滑入'], ['SlideOut', '滑出']], value => videoDraft.value.video_transition_mode = value),
          formNumber('单段秒数', videoDraft.value.video_clip_duration, value => videoDraft.value.video_clip_duration = value, 1, 30),
          formNumber('生成数量', videoDraft.value.video_count, value => videoDraft.value.video_count = value, 1, 10),
          formNumber('语速', videoDraft.value.voice_rate, value => videoDraft.value.voice_rate = value, 0.5, 2, 0.1),
          formNumber('音量', videoDraft.value.voice_volume, value => videoDraft.value.voice_volume = value, 0, 2, 0.1),
          formNumber('背景音乐音量', videoDraft.value.bgm_volume, value => videoDraft.value.bgm_volume = value, 0, 1, 0.05),
          formSelect('字幕位置', videoDraft.value.subtitle_position, [['bottom', '底部'], ['center', '居中'], ['top', '顶部'], ['custom', '自定义']], value => videoDraft.value.subtitle_position = value),
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

    function renderAssetsPage() {
      return h('section', { class: 'pane table-workspace content-assets-page' }, [
        h('div', { class: 'table-library-bar' }, [
          sectionTitle({ title: '内容资产', subtitle: `${assets.value.length} 项客户自有素材`, icon: Collection, tone: 'amber' }),
          h('div', { class: 'table-filters content-asset-filters' }, [
            h('input', { placeholder: '搜索资产名称', value: assetFilter.value.search, onInput: (event: Event) => assetFilter.value.search = (event.target as HTMLInputElement).value }),
            h('button', { class: 'filter-button', onClick: loadAssets }, '搜索'),
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
        visibleAssets.value.length
          ? h('div', { class: 'content-asset-grid' }, visibleAssets.value.map(renderAssetCard))
          : emptyState({ title: `暂无${assetSegmentLabel(assetSegment.value)}资产`, description: '点击右上角按钮导入，克隆音频也可以直接录制', icon: Collection, tone: 'amber' }),
      ])
    }

    function renderAssetCard(asset: Dict) {
      return h('article', { class: 'content-asset-card' }, [
        h('div', { class: 'content-asset-preview' }, [renderAssetPreview(asset)]),
        h('div', { class: 'content-asset-info' }, [
          h('strong', { title: asset.name }, String(asset.name || '未命名')),
          h('span', `${assetCategoryLabel(asset)} · ${formatBytes(asset.file_size)}`),
          h('small', asset.duration ? `${Number(asset.duration).toFixed(1)}秒` : asset.width ? `${asset.width}×${asset.height}` : '等待使用时校验'),
        ]),
        h('div', { class: 'task-card-actions' }, [
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
      return h('section', { class: 'content-voice-library' }, [
        sectionTitle({ title: '克隆音色', subtitle: `${voiceProfiles.value.length} 个可复用音色`, icon: MagicStick, tone: 'purple', compact: true }),
        voiceProfiles.value.length
          ? h('div', { class: 'content-voice-grid' }, voiceProfiles.value.map(profile => h('article', { class: 'content-voice-card' }, [
              h('audio', { src: profile.reference_asset?.preview_url, controls: true, preload: 'metadata' }),
              h('div', { class: 'content-voice-info' }, [
                h('strong', String(profile.name || '未命名音色')),
                h('span', `VoxCPM2 · ${profile.reference_asset?.name || '参考音频'}`),
                h('small', profile.prompt_text ? '高保真克隆' : '普通克隆'),
              ]),
              h('div', { class: 'task-card-actions' }, [
                h('button', { class: 'text-icon-button', onClick: () => renameVoiceProfile(profile) }, '重命名'),
                h('button', { class: 'text-icon-button danger', onClick: () => deleteVoiceProfile(profile) }, '删除'),
              ]),
            ])))
          : h('p', { class: 'content-voice-empty' }, '从下方音频资产创建克隆音色，视频和后续数字人可以共用。'),
      ])
    }

    function voiceOptionLabel(value: string) {
      if (!value.startsWith('voxcpm2:')) return voiceLabel(value)
      const profile = voiceProfiles.value.find(item => `voxcpm2:${item.id}` === value)
      return profile ? String(profile.name) : 'VoxCPM2 克隆音色'
    }

    function renderRecordsPage() {
      return h(SplitPane, { class: 'content-card-split', storageKey: 'content-records', side: 'right', defaultSideWidth: 480, minSideWidth: 380 }, {
        default: () => h('section', { class: 'pane content-pane content-record-gallery' }, [
          sectionTitle({
            title: '生成内容',
            subtitle: `${generatedItems.value.length} 个视频成品`,
            icon: VideoPlay,
            tone: 'purple',
            aside: h('div', { class: 'content-record-toolbar' }, [
              h('input', { placeholder: '搜索主题或文件名', value: recordSearch.value, onInput: (event: Event) => recordSearch.value = (event.target as HTMLInputElement).value }),
              h('button', { class: 'primary-action', onClick: () => router.push('/content-create') }, [h(Plus, { class: 'inline-icon' }), '新增视频']),
            ]),
          }),
          generatedItems.value.length
            ? h('div', { class: 'content-generated-grid' }, generatedItems.value.map(renderGeneratedCard))
            : emptyState({ title: '暂无生成内容', description: '生成成功的视频会统一展示在这里', icon: VideoPlay }),
        ]),
        side: () => h('aside', { class: 'pane side-pane content-record-list' }, [
          sectionTitle({ title: '生成记录', subtitle: `共 ${jobs.value.total || 0} 条`, icon: Tickets, tone: 'blue', aside: h('button', { class: 'icon-refresh', onClick: () => loadJobs(true) }, [h(Refresh)]) }),
          renderJobList(jobs.value.items || []),
          renderJobPagination(),
        ]),
      })
    }

    function renderGeneratedCard(item: Dict) {
      const job = item.job as Dict
      const output = item.output as Dict
      return h('article', { class: 'content-generated-card' }, [
        h('video', { src: output.url, controls: true, preload: 'metadata' }),
        h('div', { class: 'content-generated-info' }, [
          h('strong', { title: job.subject }, String(job.subject || '未命名视频')),
          h('span', `${output.name || '生成视频'} · 第${job.attempt || 1}次生成`),
          h('time', `生成于 ${job.finished_at || job.created_at || '-'}`),
        ]),
        h('div', { class: 'content-generated-actions' }, [
          h('select', {
            value: output.upload_status || 'not_uploaded',
            onChange: (event: Event) => updateOutputStatus(job, output, (event.target as HTMLSelectElement).value),
          }, [h('option', { value: 'not_uploaded' }, '未上传'), h('option', { value: 'uploaded' }, '已上传')]),
          h('button', { class: 'primary-soft', onClick: () => uploadOutput(job, output) }, '上传'),
          h('a', { class: 'primary-soft', href: output.url, download: output.name }, '下载'),
          h('button', { class: 'text-icon-button', onClick: () => renameJob(job) }, '编辑'),
          h('button', { class: 'text-icon-button danger', onClick: () => archiveJob(String(job.id)) }, '删除'),
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
        h('button', { class: 'text-icon-button', onClick: () => renameJob(job) }, '编辑'),
        ['queued', 'running'].includes(String(job.status)) ? h('button', { class: 'danger-soft', onClick: () => cancelJob(String(job.id)) }, '取消') : null,
        ['failed', 'cancelled', 'interrupted'].includes(String(job.status)) ? h('button', { class: 'primary-soft', onClick: () => retryJob(String(job.id)) }, '重试') : null,
        job.status === 'succeeded' ? h('button', { class: 'primary-soft', onClick: () => publishJob(String(job.id)) }, '发布') : null,
        !['queued', 'running'].includes(String(job.status)) ? h('button', { class: 'text-icon-button danger', onClick: () => archiveJob(String(job.id)) }, '删除') : null,
      ])
    }

    function renderJobPagination() {
      const page = Number(jobs.value.page || 1)
      const pages = Math.max(1, Math.ceil(Number(jobs.value.total || 0) / Number(jobs.value.page_size || 20)))
      return h('div', { class: 'table-page-controls content-job-pagination' }, [
        h('button', { disabled: page <= 1, onClick: () => { jobs.value.page = page - 1; void loadJobs(true) } }, '上一页'),
        h('span', `${page} / ${pages}`),
        h('button', { disabled: page >= pages, onClick: () => { jobs.value.page = page + 1; void loadJobs(true) } }, '下一页'),
      ])
    }

    function renderSettingsPage() {
      return h(SplitPane, { class: 'content-card-split', storageKey: 'content-settings', side: 'right', defaultSideWidth: 340, minSideWidth: 300 }, {
        default: () => h('section', { class: 'pane content-pane content-settings-pane' }, [
          sectionTitle({ title: '内容设置', subtitle: '视频AI与拓客AI完全独立', icon: Setting, tone: 'teal', aside: h('button', { class: 'primary-action', onClick: saveSettings }, '保存设置') }),
          ...settingGroups.map(group => h('details', { class: 'settings-fold', open: group.title === '素材与基础处理' }, [
            h('summary', [h('strong', group.title), h('span', `${group.fields.length} 项配置`)]),
            h('div', { class: 'settings-fold-body form-grid content-settings-grid' }, group.fields.map(renderSettingField)),
          ])),
        ]),
        side: () => h('aside', { class: 'pane side-pane content-env-pane' }, [
          sectionTitle({ title: '内容环境', subtitle: environment.value.ok ? '可以生成视频' : '存在缺失项', icon: Setting, tone: environment.value.ok ? 'green' : 'amber', aside: h('button', { class: 'icon-refresh', onClick: loadEnvironment }, [h(Refresh)]) }),
          renderEnvironment(),
        ]),
      })
    }

    function renderSettingField(field: SettingField) {
      const value = getPath(settingsDraft.value, field.path)
      if (field.type === 'boolean') return formSelect(field.label, String(Boolean(value)), [['true', '开启'], ['false', '关闭']], selected => setPath(settingsDraft.value, field.path, selected === 'true'))
      if (field.options) return formSelect(field.label, String(value ?? ''), field.options, selected => setPath(settingsDraft.value, field.path, selected))
      if (field.type === 'list') return formTextarea(field.label, Array.isArray(value) ? value.join('\n') : String(value || ''), selected => setPath(settingsDraft.value, field.path, selected.split(/[\n,]+/).map(item => item.trim()).filter(Boolean)))
      if (field.type === 'textarea') return formTextarea(field.label, String(value || ''), selected => setPath(settingsDraft.value, field.path, selected), 'field-full')
      if (field.type === 'number') return formNumber(field.label, Number(value || 0), selected => setPath(settingsDraft.value, field.path, selected))
      return formInput(field.label, String(value ?? ''), selected => setPath(settingsDraft.value, field.path, selected), '', field.type === 'password' ? 'password' : 'text')
    }

    function renderEnvironment() {
      const deps = environment.value.dependencies || {}
      return h('div', { class: 'content-env-list' }, [
        environmentRow('FFmpeg', environment.value.ffmpeg?.ok, environment.value.ffmpeg?.path || '未找到'),
        environmentRow('字幕字体', environment.value.fonts?.ok, `${environment.value.fonts?.count || 0} 个`),
        environmentRow('Whisper模型', environment.value.whisper?.downloaded, environment.value.whisper?.downloaded ? '已下载' : '首次使用时自动下载'),
        environmentRow('VoxCPM2', environment.value.voice_models?.voxcpm2?.installed && environment.value.voice_models?.voxcpm2?.downloaded, environment.value.voice_models?.voxcpm2?.downloaded ? '模型已就绪' : '依赖或模型未安装'),
        ...Object.entries(deps).map(([name, ok]) => environmentRow(name, Boolean(ok), ok ? '已安装' : '缺失')),
        h('div', { class: 'content-disk-info' }, `可用磁盘：${formatBytes(environment.value.disk?.free || 0)}`),
      ])
    }

    function environmentRow(label: string, ok: boolean, detail: string) {
      return h('div', { class: ['content-env-row', ok ? 'ok' : 'warn'] }, [h('strong', label), h('span', detail)])
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
      if (view.value === 'content-create') return renderCreatePage()
      if (view.value === 'content-assets') return renderAssetsPage()
      if (view.value === 'content-records') return renderRecordsPage()
      return renderSettingsPage()
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

function assetMatchesSegment(asset: Dict, segment: string) {
  if (segment === 'all') return true
  if (segment === 'video' || segment === 'image') return asset.asset_type === segment
  if (segment === 'voice_reference') return asset.asset_type === 'audio' && asset.purpose === 'voice_reference'
  return asset.asset_type === 'audio' && asset.purpose !== 'voice_reference'
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

function voiceLabel(value: string) {
  return value.replace('-Female', ' 女声').replace('-Male', ' 男声').replace('Neural', '')
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
