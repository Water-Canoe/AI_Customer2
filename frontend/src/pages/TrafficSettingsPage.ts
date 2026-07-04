import { defineComponent, h, onMounted, reactive, ref } from 'vue'
import { Check, Key, Picture, Refresh, Setting, UploadFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

import { LicenseDialog } from '../components/ui/LicenseDialog'
import { emptyState, pageAction, sectionTitle } from '../components/ui/Workbench'
import { api } from '../shared/api'
import type { Dict } from '../shared/types'

// 前端默认值和后端 DEFAULT_SETTINGS 保持一致，接口未返回时也能维持可编辑表单。
const defaultSettings: Dict = {
  traffic_per_run_limit: 20,
  traffic_daily_limit: 100,
  traffic_stay_seconds_min: 6,
  traffic_stay_seconds_max: 15,
  traffic_action_interval_seconds_min: 1,
  traffic_action_interval_seconds_max: 3,
  traffic_action_like: true,
  traffic_action_follow: false,
  traffic_action_comment: true,
  traffic_comment_templates_text: '想了解一下，方便看下主页吗？',
  traffic_only_active_video: false,
  traffic_active_comment_min: 5,
  traffic_video_block_keywords_text: '',
  traffic_author_block_keywords_text: '',
  traffic_rule_relation: 'or',
  traffic_match_rules: [],
}

export default defineComponent({
  name: 'TrafficSettingsPage',
  setup() {
    const loading = ref(false)
    const saving = ref(false)
    const uploading = ref(false)
    const assets = ref<Dict[]>([])
    const licenseDialogOpen = ref(false)
    const licenseLoading = ref(false)
    const licenseChecking = ref(false)
    const licenseInfo = ref<Dict>({})
    const licenseCodeDraft = ref('')
    const local = reactive<Dict>({ ...defaultSettings })

    async function loadAll() {
      loading.value = true
      try {
        const [settingsResponse, assetsResponse] = await Promise.all([
          api.get('/traffic/settings'),
          api.get('/traffic/assets'),
        ])
        applySettings(local, settingsResponse.data)
        assets.value = assetsResponse.data || []
      } finally {
        loading.value = false
      }
    }

    async function openLicenseDialog() {
      licenseDialogOpen.value = true
      licenseLoading.value = true
      try {
        const { data } = await api.get('/traffic/license')
        licenseInfo.value = data
        licenseCodeDraft.value = String(data.license_code || '')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '引流授权信息加载失败')
      } finally {
        licenseLoading.value = false
      }
    }

    async function saveLicense() {
      licenseChecking.value = true
      try {
        const { data } = await api.put('/traffic/license', { license_code: licenseCodeDraft.value })
        licenseInfo.value = data
        licenseCodeDraft.value = String(data.license_code || '')
        ElMessage.success('引流授权码已保存')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '引流授权码保存失败')
      } finally {
        licenseChecking.value = false
      }
    }

    async function checkLicense() {
      licenseChecking.value = true
      try {
        const { data } = await api.post('/traffic/license/check', { license_code: licenseCodeDraft.value })
        licenseInfo.value = data
        licenseCodeDraft.value = String(data.license_code || '')
        if (data.authorized) ElMessage.success(data.message || '引流授权校验通过')
        else ElMessage.error(data.message || '引流授权校验失败')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '引流授权校验失败')
      } finally {
        licenseChecking.value = false
      }
    }

    async function copyDeviceCode() {
      const code = String(licenseInfo.value.device_code || '').trim()
      if (!code) {
        ElMessage.warning('当前没有可复制的设备码')
        return
      }
      await navigator.clipboard.writeText(code)
      ElMessage.success('设备码已复制')
    }

    async function saveSettings() {
      saving.value = true
      try {
        const payload = {
          traffic_per_run_limit: Number(local.traffic_per_run_limit || 20),
          traffic_daily_limit: Number(local.traffic_daily_limit || 100),
          traffic_stay_seconds_min: Number(local.traffic_stay_seconds_min || 0),
          traffic_stay_seconds_max: Number(local.traffic_stay_seconds_max || 0),
          traffic_action_interval_seconds_min: Number(local.traffic_action_interval_seconds_min || 0),
          traffic_action_interval_seconds_max: Number(local.traffic_action_interval_seconds_max || 0),
          traffic_action_like: Boolean(local.traffic_action_like),
          traffic_action_follow: Boolean(local.traffic_action_follow),
          traffic_action_comment: Boolean(local.traffic_action_comment),
          traffic_comment_templates: splitTemplates(String(local.traffic_comment_templates_text || '')),
          traffic_only_active_video: Boolean(local.traffic_only_active_video),
          traffic_active_comment_min: Number(local.traffic_active_comment_min || 0),
          traffic_video_block_keywords: splitTemplates(String(local.traffic_video_block_keywords_text || '')),
          traffic_author_block_keywords: splitTemplates(String(local.traffic_author_block_keywords_text || '')),
          traffic_rule_relation: local.traffic_rule_relation === 'and' ? 'and' : 'or',
          traffic_match_rules: normalizeRules(local.traffic_match_rules),
        }
        const { data } = await api.put('/traffic/settings', { values: payload })
        applySettings(local, data)
        ElMessage.success('引流设置已保存')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '引流设置保存失败')
      } finally {
        saving.value = false
      }
    }

    async function uploadAsset(event: Event) {
      const input = event.target as HTMLInputElement
      const file = input.files?.[0]
      if (!file) return
      uploading.value = true
      try {
        const dataUrl = await readFileAsDataUrl(file)
        await api.post('/traffic/assets', { name: file.name, data_url: dataUrl })
        const { data } = await api.get('/traffic/assets')
        assets.value = data || []
        ElMessage.success('图片素材已上传')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '图片上传失败')
      } finally {
        input.value = ''
        uploading.value = false
      }
    }

    onMounted(loadAll)

    return () => h('section', { class: 'traffic-settings-page' }, [
      pageAction({
        title: '引流工作台设置',
        description: '引流授权、执行参数、文案和图片素材独立管理，不与拓客工作台共用授权码。',
        icon: Setting,
        tone: 'teal',
        aside: [
          h('button', { class: 'secondary-action', onClick: openLicenseDialog }, [h(Key, { class: 'inline-icon' }), '授权与设备']),
          h('button', { class: 'secondary-action', disabled: loading.value, onClick: loadAll }, [h(Refresh, { class: 'inline-icon' }), '刷新']),
        ],
      }),
      h('div', { class: 'traffic-settings-grid' }, [
        h('section', { class: 'traffic-panel' }, [
          sectionTitle({ title: '执行参数', subtitle: '定向引流和随机引流启动批次时统一使用', icon: Setting, tone: 'teal', compact: true }),
          renderSettingsForm(local),
          h('div', { class: 'action-row' }, [
            h('button', { class: 'primary-action', disabled: saving.value, onClick: saveSettings }, [h(Check, { class: 'inline-icon' }), saving.value ? '保存中' : '保存设置']),
            h('button', { class: 'secondary-action', onClick: openLicenseDialog }, [h(Key, { class: 'inline-icon' }), '授权与设备']),
          ]),
        ]),
        h('section', { class: 'traffic-panel' }, [
          sectionTitle({ title: '规则过滤', subtitle: '规则命中后才会进入队列或执行', icon: Check, tone: 'green', compact: true }),
          renderRuleForm(local),
        ]),
      ]),
      h('section', { class: 'traffic-panel' }, [
        sectionTitle({
          title: '文案与图片素材',
          subtitle: '一行一条文案，每次发送时随机选择；图片素材在计划中选择',
          icon: Picture,
          tone: 'blue',
          compact: true,
          aside: renderUploadButton(uploading.value, uploadAsset),
        }),
        h('div', { class: 'traffic-copy-grid' }, [
          field('多文案', h('textarea', {
            value: local.traffic_comment_templates_text,
            rows: 6,
            placeholder: '一行一条，每次发送时随机选择',
            onInput: (event: Event) => local.traffic_comment_templates_text = (event.target as HTMLTextAreaElement).value,
          })),
          renderAssets(assets.value),
        ]),
      ]),
      h(LicenseDialog, {
        open: licenseDialogOpen.value,
        loading: licenseLoading.value,
        checking: licenseChecking.value,
        info: licenseInfo.value,
        code: licenseCodeDraft.value,
        placeholder: '填写引流工作台授权码',
        'onUpdate:code': (value: string) => licenseCodeDraft.value = value,
        onClose: () => licenseDialogOpen.value = false,
        onSave: saveLicense,
        onCheck: checkLicense,
        onCopyDevice: copyDeviceCode,
      }),
    ])
  }
})

function applySettings(local: Dict, data: Dict) {
  const templates = Array.isArray(data.traffic_comment_templates) ? data.traffic_comment_templates : []
  const videoBlocks = Array.isArray(data.traffic_video_block_keywords) ? data.traffic_video_block_keywords : []
  const authorBlocks = Array.isArray(data.traffic_author_block_keywords) ? data.traffic_author_block_keywords : []
  Object.assign(local, {
    ...defaultSettings,
    ...data,
    traffic_comment_templates_text: templates.join('\n') || defaultSettings.traffic_comment_templates_text,
    traffic_video_block_keywords_text: videoBlocks.join('\n'),
    traffic_author_block_keywords_text: authorBlocks.join('\n'),
    traffic_match_rules: normalizeRules(data.traffic_match_rules),
  })
}

function renderSettingsForm(local: Dict) {
  return h('div', { class: 'traffic-form two-column' }, [
    field('每轮上限', numberInput(local, 'traffic_per_run_limit', 1, 100)),
    field('每日上限', numberInput(local, 'traffic_daily_limit', 1, 500)),
    field('停留秒数', pairInputs(local, 'traffic_stay_seconds_min', 'traffic_stay_seconds_max', 0, 300)),
    field('动作间隔秒数', pairInputs(local, 'traffic_action_interval_seconds_min', 'traffic_action_interval_seconds_max', 0, 120)),
    field('默认辅助动作', h('div', { class: 'traffic-checks' }, [
      check(local, 'traffic_action_like', '点赞'),
      check(local, 'traffic_action_follow', '关注作者'),
    ])),
  ])
}

function renderRuleForm(local: Dict) {
  // 规则保持扁平编辑；递归规则组等到单计划需要多套话术时再加。
  const rules = ensureRules(local)
  return h('div', { class: 'traffic-form' }, [
    field('活跃视频', h('div', { class: 'traffic-inline' }, [
      check(local, 'traffic_only_active_video', '只执行活跃视频'),
      numberInput(local, 'traffic_active_comment_min', 0, 100000),
    ])),
    field('作者屏蔽词', h('textarea', {
      value: local.traffic_author_block_keywords_text,
      rows: 3,
      onInput: (event: Event) => local.traffic_author_block_keywords_text = (event.target as HTMLTextAreaElement).value,
    })),
    field('视频屏蔽词', h('textarea', {
      value: local.traffic_video_block_keywords_text,
      rows: 3,
      onInput: (event: Event) => local.traffic_video_block_keywords_text = (event.target as HTMLTextAreaElement).value,
    })),
    field('命中关系', h('select', {
      value: local.traffic_rule_relation,
      onChange: (event: Event) => local.traffic_rule_relation = (event.target as HTMLSelectElement).value,
    }, [
      h('option', { value: 'or' }, '任一命中'),
      h('option', { value: 'and' }, '全部命中'),
    ])),
    field('命中规则', h('div', { class: 'traffic-rule-list' }, [
      ...rules.map((rule: Dict, index: number) => h('div', { class: 'traffic-rule-row' }, [
        h('select', {
          value: rule.field || 'title',
          onChange: (event: Event) => rule.field = (event.target as HTMLSelectElement).value,
        }, [
          h('option', { value: 'title' }, '视频内容'),
          h('option', { value: 'author' }, '作者昵称'),
          h('option', { value: 'keyword' }, '来源关键词'),
        ]),
        h('input', {
          value: rule.keyword || '',
          placeholder: '关键词',
          onInput: (event: Event) => rule.keyword = (event.target as HTMLInputElement).value,
        }),
        h('button', { class: 'secondary-action danger-action', type: 'button', onClick: () => removeRule(local, index) }, '删除'),
      ])),
      h('button', { class: 'secondary-action', type: 'button', onClick: () => addRule(local) }, '添加条件'),
    ])),
  ])
}

function renderUploadButton(uploading: boolean, uploadAsset: (event: Event) => void) {
  return h('label', { class: ['secondary-action', 'traffic-upload-button'] }, [
    h(UploadFilled, { class: 'inline-icon' }),
    uploading ? '上传中' : '上传图片',
    h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp', disabled: uploading, onChange: uploadAsset }),
  ])
}

function renderAssets(assets: Dict[]) {
  if (!assets.length) {
    return emptyState({ title: '暂无图片素材', description: '上传 PNG、JPG 或 WEBP 后，可在计划中选择关联。', icon: Picture, tone: 'gray' })
  }
  return h('div', { class: 'traffic-asset-list' }, assets.map(asset => h('div', { class: 'traffic-asset-item' }, [
    h('strong', asset.name || asset.file_name),
    h('span', `${asset.mime_type || '-'} · ${formatBytes(Number(asset.size_bytes || 0))}`),
    h('small', asset.created_at || ''),
  ])))
}

function field(label: string, control: any) {
  return h('label', { class: 'traffic-field' }, [h('span', label), control])
}

function numberInput(local: Dict, key: string, min: number, max: number) {
  return h('input', {
    type: 'number',
    min,
    max,
    value: local[key],
    onInput: (event: Event) => local[key] = (event.target as HTMLInputElement).value,
  })
}

function pairInputs(local: Dict, leftKey: string, rightKey: string, min: number, max: number) {
  return h('div', { class: 'traffic-inline' }, [
    h('input', { type: 'number', min, max, value: local[leftKey], onInput: (event: Event) => local[leftKey] = (event.target as HTMLInputElement).value }),
    h('input', { type: 'number', min, max, value: local[rightKey], onInput: (event: Event) => local[rightKey] = (event.target as HTMLInputElement).value }),
  ])
}

function check(local: Dict, key: string, label: string) {
  return h('label', { class: 'traffic-check' }, [
    h('input', { type: 'checkbox', checked: Boolean(local[key]), onChange: (event: Event) => local[key] = (event.target as HTMLInputElement).checked }),
    label,
  ])
}

function splitTemplates(value: string) {
  return value.split(/\r?\n/).map(item => item.trim()).filter(Boolean)
}

function ensureRules(local: Dict) {
  if (!Array.isArray(local.traffic_match_rules)) local.traffic_match_rules = []
  return local.traffic_match_rules
}

function addRule(local: Dict) {
  local.traffic_match_rules = [...ensureRules(local), { field: 'title', keyword: '' }]
}

function removeRule(local: Dict, index: number) {
  local.traffic_match_rules = ensureRules(local).filter((_: Dict, itemIndex: number) => itemIndex !== index)
}

function normalizeRules(value: unknown) {
  if (!Array.isArray(value)) return []
  return value
    .map((item: any) => ({
      field: ['author', 'title', 'keyword'].includes(item?.field) ? item.field : 'title',
      keyword: String(item?.keyword || '').trim(),
    }))
    .filter(item => item.keyword)
}

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error || new Error('文件读取失败'))
    reader.readAsDataURL(file)
  })
}

function formatBytes(value: number) {
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  if (value >= 1024) return `${Math.round(value / 1024)} KB`
  return `${value} B`
}
