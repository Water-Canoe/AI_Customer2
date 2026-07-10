import { computed, defineComponent, h, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Collection,
  DataLine,
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
import { emptyState, metricTile, sectionTitle } from '../components/ui/Workbench'


const MASKED_SECRET = '********'

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
    const view = computed(() => String(route.name || 'content-create'))
    const assets = ref<Dict[]>([])
    const jobs = ref<Dict>({ items: [], total: 0, page: 1, page_size: 20 })
    const selectedJob = ref<Dict | null>(null)
    const settings = ref<Dict>({})
    const settingsDraft = ref<Dict>({})
    const environment = ref<Dict>({})
    const videoDraft = ref<Dict>(defaultVideoDraft())
    const selectedAssetIds = ref<string[]>([])
    const audioAssetId = ref('')
    const bgmAssetId = ref('')
    const assetFilter = ref({ type: '', search: '' })
    const voiceProvider = ref('edge')
    const voices = ref<string[]>([])
    const loading = ref(false)
    const uploading = ref(false)
    const draggedAssetId = ref('')
    let refreshTimer = 0

    const materialAssets = computed(() => assets.value.filter(item => ['video', 'image'].includes(String(item.asset_type))))
    const audioAssets = computed(() => assets.value.filter(item => String(item.asset_type) === 'audio'))
    const selectedMaterials = computed(() => selectedAssetIds.value
      .map(id => materialAssets.value.find(item => item.id === id))
      .filter(Boolean) as Dict[])
    const activeJobs = computed(() => (jobs.value.items || []).filter((job: Dict) => ['queued', 'running'].includes(String(job.status))))

    onMounted(() => {
      void loadPage()
      refreshTimer = window.setInterval(() => {
        if (activeJobs.value.length && ['content-create', 'content-records'].includes(view.value)) void loadJobs(false)
      }, 3000)
    })
    onUnmounted(() => window.clearInterval(refreshTimer))
    watch(view, () => void loadPage())
    watch(() => props.refreshSeq, () => void loadPage())

    async function loadPage() {
      if (view.value === 'content-create') await Promise.all([loadAssets(), loadJobs(false), loadVoices()])
      else if (view.value === 'content-assets') await loadAssets()
      else if (view.value === 'content-records') await loadJobs(true)
      else await Promise.all([loadSettings(), loadEnvironment()])
    }

    async function loadAssets() {
      const { data } = await api.get('/content/assets', { params: { asset_type: assetFilter.value.type, search: assetFilter.value.search } })
      assets.value = data
      selectedAssetIds.value = selectedAssetIds.value.filter(id => data.some((item: Dict) => item.id === id))
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

    async function selectJob(id: string) {
      const { data } = await api.get(`/content/video-jobs/${id}`)
      selectedJob.value = data
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
        const { data } = await api.post('/content/assets/import', form)
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
        await ElMessageBox.confirm(`确认删除“${asset.name}”？正在使用的资产不会被删除。`, '删除内容资产', { type: 'warning' })
        await api.delete(`/content/assets/${asset.id}`)
        ElMessage.success('资产已删除')
        await loadAssets()
      } catch (error: any) {
        if (isUserCancel(error)) return
        ElMessage.error(error?.response?.data?.detail || '资产删除失败')
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
        await api.delete(`/content/video-jobs/${id}`)
        ElMessage.success('生成记录已归档，成品文件继续保留')
        if (selectedJob.value?.id === id) selectedJob.value = null
        await loadJobs(true)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '归档失败')
      }
    }

    async function publishJob(id: string) {
      try {
        const { data } = await api.post(`/content/video-jobs/${id}/publish`)
        const failed = (data.results || []).filter((item: Dict) => !item.success).length
        if (failed) ElMessage.warning(`发布完成，${failed} 项失败`)
        else ElMessage.success('视频发布成功')
        await selectJob(id)
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '视频发布失败')
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
            formSelect('音色供应商', voiceProvider.value, [['edge', 'Edge TTS'], ['azure-v2', 'Azure Speech'], ['siliconflow', '硅基流动'], ['gemini', 'Gemini TTS'], ['mimo', 'MiMo TTS'], ['elevenlabs', 'ElevenLabs'], ['chatterbox', 'Chatterbox']], value => { voiceProvider.value = value; void loadVoices() }),
            voices.value.length
              ? formSelect('配音音色', videoDraft.value.voice_name, voices.value.map(value => [value, voiceLabel(value)]), value => videoDraft.value.voice_name = value)
              : formInput('配音音色', videoDraft.value.voice_name, value => videoDraft.value.voice_name = value),
            formSelect('自定义配音', audioAssetId.value, [['', '使用TTS'], ...audioAssets.value.map((item): [string, string] => [String(item.id), String(item.name)])], value => audioAssetId.value = value),
            formSelect('背景音乐', bgmAssetId.value, [['', '不使用背景音乐'], ...audioAssets.value.map((item): [string, string] => [String(item.id), String(item.name)])], value => bgmAssetId.value = value),
            renderAdvancedVideoOptions(),
          ]),
          h('div', { class: 'task-card-actions content-create-actions' }, [
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
      return h('details', { class: 'settings-fold field-full' }, [
        h('summary', [h('strong', '高级视频参数'), h('span', '转场、速度、字幕和样式')]),
        h('div', { class: 'settings-fold-body form-grid' }, [
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
          h('div', { class: 'table-filters' }, [
            h('input', { placeholder: '搜索资产名称', value: assetFilter.value.search, onInput: (event: Event) => assetFilter.value.search = (event.target as HTMLInputElement).value }),
            h('select', { value: assetFilter.value.type, onChange: (event: Event) => assetFilter.value.type = (event.target as HTMLSelectElement).value }, [
              h('option', { value: '' }, '全部类型'),
              h('option', { value: 'video' }, '视频'),
              h('option', { value: 'image' }, '图片'),
              h('option', { value: 'audio' }, '音频'),
            ]),
            h('button', { class: 'filter-button', onClick: loadAssets }, '筛选'),
            h('label', { class: ['primary-action', uploading.value ? 'disabled' : ''] }, [
              h(Plus, { class: 'inline-icon' }),
              uploading.value ? '导入中...' : '导入内容资产',
              h('input', { type: 'file', multiple: true, accept: 'video/*,image/*,audio/*', disabled: uploading.value, onChange: uploadAssets, hidden: true }),
            ]),
          ]),
        ]),
        assets.value.length
          ? h('div', { class: 'content-asset-grid' }, assets.value.map(renderAssetCard))
          : emptyState({ title: '暂无内容资产', description: '导入客户自己的视频、图片或音频', icon: Collection, tone: 'amber' }),
      ])
    }

    function renderAssetCard(asset: Dict) {
      return h('article', { class: 'content-asset-card' }, [
        h('div', { class: 'content-asset-preview' }, [renderAssetPreview(asset)]),
        h('div', { class: 'content-asset-info' }, [
          h('strong', { title: asset.name }, String(asset.name || '未命名')),
          h('span', `${assetTypeLabel(asset.asset_type)} · ${formatBytes(asset.file_size)}`),
          h('small', asset.duration ? `${Number(asset.duration).toFixed(1)}秒` : asset.width ? `${asset.width}×${asset.height}` : '等待使用时校验'),
        ]),
        h('div', { class: 'task-card-actions' }, [
          h('button', { class: 'text-icon-button', onClick: () => renameAsset(asset) }, '重命名'),
          h('button', { class: 'text-icon-button danger', onClick: () => deleteAsset(asset) }, [h(Delete, { class: 'inline-icon' }), '删除']),
        ]),
      ])
    }

    function renderRecordsPage() {
      return h(SplitPane, { class: 'content-card-split', storageKey: 'content-records', side: 'right', defaultSideWidth: 480, minSideWidth: 380 }, {
        default: () => h('section', { class: 'pane content-pane content-record-detail' }, [
          sectionTitle({ title: '生成详情', subtitle: selectedJob.value?.subject || '选择右侧记录', icon: VideoPlay, tone: 'purple' }),
          selectedJob.value ? renderJobDetail(selectedJob.value) : emptyState({ title: '请选择生成记录', description: '查看进度、错误和生成视频', icon: VideoPlay }),
        ]),
        side: () => h('aside', { class: 'pane side-pane content-record-list' }, [
          sectionTitle({ title: '生成记录', subtitle: `共 ${jobs.value.total || 0} 条`, icon: Tickets, tone: 'blue', aside: h('button', { class: 'icon-refresh', onClick: () => loadJobs(true) }, [h(Refresh)]) }),
          renderJobList(jobs.value.items || []),
          renderJobPagination(),
        ]),
      })
    }

    function renderJobList(rows: Dict[]) {
      if (!rows.length) return emptyState({ title: '暂无视频任务', description: '在视频创作页面创建第一条任务', icon: Tickets })
      return h('div', { class: 'content-job-list' }, rows.map(job => h('button', {
        class: ['content-job-card', selectedJob.value?.id === job.id ? 'selected' : ''],
        onClick: () => selectJob(String(job.id)),
      }, [
        h('div', { class: 'content-job-head' }, [h('strong', String(job.subject || '未命名视频')), h('span', { class: `status-pill status-${job.status}` }, statusLabel(job.status))]),
        h('small', `${stageLabel(job.current_stage)} · ${job.progress || 0}% · 第${job.attempt || 0}次`),
        h('div', { class: 'content-job-progress' }, [h('span', { style: { width: `${Number(job.progress || 0)}%` } })]),
        job.error ? h('p', { class: 'content-job-error' }, String(job.error)) : null,
      ])))
    }

    function renderJobDetail(job: Dict) {
      return h('div', { class: 'content-job-detail-body' }, [
        h('div', { class: 'content-job-metrics' }, [
          metricTile({ label: '状态', value: statusLabel(job.status), icon: DataLine, tone: statusTone(job.status) }),
          metricTile({ label: '进度', value: `${job.progress || 0}%`, icon: VideoPlay, tone: 'blue' }),
          metricTile({ label: '尝试', value: job.attempt || 0, icon: Refresh, tone: 'amber' }),
        ]),
        h('div', { class: 'content-job-stage' }, [h('strong', stageLabel(job.current_stage)), h('span', `${job.progress || 0}%`)]),
        job.error ? h('div', { class: 'bulk-preview-warning' }, [h('li', String(job.error))]) : null,
        (job.outputs || []).length
          ? h('div', { class: 'content-output-grid' }, job.outputs.map((output: Dict) => h('article', { class: 'content-output-card' }, [
              h('video', { src: output.url, controls: true, preload: 'metadata' }),
              h('strong', String(output.name || '生成视频')),
              h('a', { class: 'primary-soft', href: output.url, download: output.name }, '下载视频'),
            ])))
          : emptyState({ title: '尚未生成成品', description: ['queued', 'running'].includes(String(job.status)) ? '任务完成后会在这里显示视频' : '可查看错误后重试', icon: VideoPlay }),
        (job.publish_results || []).length ? h('div', { class: 'content-publish-results' }, [
          sectionTitle({ title: '发布结果', subtitle: `${job.publish_results.length} 项`, icon: DataLine, tone: 'green', compact: true }),
          ...job.publish_results.map((item: Dict) => h('p', { class: item.success ? 'success-text' : 'danger-text' }, item.success ? `发布成功：${item.request_id || '-'}` : `发布失败：${item.error || item.message || '-'}`)),
        ]) : null,
        h('div', { class: 'task-card-actions content-job-actions' }, [
          ['queued', 'running'].includes(String(job.status)) ? h('button', { class: 'danger-soft', onClick: () => cancelJob(String(job.id)) }, '取消任务') : null,
          ['failed', 'cancelled', 'interrupted'].includes(String(job.status)) ? h('button', { class: 'primary-action', onClick: () => retryJob(String(job.id)) }, '重新生成') : null,
          job.status === 'succeeded' ? h('button', { class: 'primary-action', onClick: () => publishJob(String(job.id)) }, '发布视频') : null,
          !['queued', 'running'].includes(String(job.status)) ? h('button', { class: 'secondary-action', onClick: () => archiveJob(String(job.id)) }, '归档记录') : null,
        ]),
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

function assetTypeLabel(value: unknown) {
  return ({ video: '视频', image: '图片', audio: '音频' } as Record<string, string>)[String(value || '')] || '文件'
}

function statusLabel(value: unknown) {
  return ({ queued: '排队中', running: '生成中', succeeded: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' } as Record<string, string>)[String(value || '')] || String(value || '-')
}

function stageLabel(value: unknown) {
  return ({ queued: '等待执行', preparing: '准备环境', script: '生成文案', terms: '生成素材词', audio: '生成配音', subtitle: '生成字幕', materials: '准备素材', rendering: '合成视频', completed: '生成完成', failed: '生成失败', cancelled: '已取消', interrupted: '已中断' } as Record<string, string>)[String(value || '')] || String(value || '-')
}

function statusTone(value: unknown): 'green' | 'red' | 'blue' | 'amber' | 'gray' {
  if (value === 'succeeded') return 'green'
  if (value === 'failed') return 'red'
  if (value === 'running') return 'blue'
  if (value === 'queued') return 'amber'
  return 'gray'
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
