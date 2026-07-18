import { defineComponent, h, onMounted, onUnmounted, ref, watch, type PropType } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Key, Plus, Refresh, User } from '@element-plus/icons-vue'

import { api } from '../shared/api'
import type { Dict } from '../shared/types'
import { emptyState, sectionTitle } from '../components/ui/Workbench'

const FEATURES = ['acquisition', 'message', 'traffic', 'publish']
const ROLE_FEATURES: Record<string, string[]> = {
  brand: ['message', 'publish'],
  service: ['message'],
  operations: ['acquisition', 'message'],
  traffic: ['acquisition', 'message', 'traffic'],
  test: FEATURES,
}

export default defineComponent({
  name: 'GlobalSettingsPage',
  props: {
    refreshSeq: { type: Number, default: 0 },
    licenseInfo: { type: Object as PropType<Dict>, default: () => ({}) },
    appVersion: { type: String, default: '' },
    appPackaged: { type: Boolean, default: false },
    updateChecking: { type: Boolean, default: false },
  },
  emits: ['open-license', 'check-update'],
  setup(props, { emit }) {
    const accounts = ref<Dict[]>([])
    const draft = ref<Dict>({ platform: 'dy', name: '', role: 'brand' })
    const loading = ref(false)
    let timer = 0

    async function loadAccounts(showError = true) {
      try {
        const { data } = await api.get('/accounts')
        accounts.value = data
      } catch (error: any) {
        if (showError) ElMessage.error(error?.response?.data?.detail || '账号列表加载失败')
      }
    }

    async function createAccount() {
      if (!String(draft.value.name || '').trim()) return ElMessage.warning('请输入账号名称')
      loading.value = true
      try {
        await api.post('/accounts', {
          ...draft.value,
          features: ROLE_FEATURES[String(draft.value.role)] || [],
          default_features: [],
        })
        draft.value.name = ''
        ElMessage.success('账号已创建，请完成扫码登录并设置默认用途')
        await loadAccounts()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '账号创建失败')
      } finally {
        loading.value = false
      }
    }

    async function updateAccount(account: Dict, values: Dict) {
      try {
        await api.patch(`/accounts/${account.id}`, values)
        await loadAccounts()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '账号修改失败')
      }
    }

    async function accountAction(account: Dict, action: 'login' | 'check') {
      try {
        await api.post(`/accounts/${account.id}/${action}`)
        ElMessage.success(action === 'login' ? '登录窗口已加入队列' : '状态检查已加入队列')
        await loadAccounts()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '账号操作失败')
      }
    }

    async function deleteAccount(account: Dict) {
      try {
        await ElMessageBox.confirm(`确认移除“${account.name}”？账号记录会停用，持久化登录目录会保留。`, '移除账号', { type: 'warning' })
        await api.delete(`/accounts/${account.id}`)
        await loadAccounts()
      } catch (error: any) {
        if (error === 'cancel' || error === 'close') return
        ElMessage.error(error?.response?.data?.detail || '账号移除失败')
      }
    }

    function toggleFeature(account: Dict, feature: string, checked: boolean) {
      const features = checked
        ? [...new Set([...(account.features || []), feature])]
        : (account.features || []).filter((value: string) => value !== feature)
      const defaultFeatures = (account.default_features || []).filter((value: string) => features.includes(value))
      void updateAccount(account, { features, default_features: defaultFeatures })
    }

    function toggleDefault(account: Dict, feature: string, checked: boolean) {
      const defaults = checked
        ? [...new Set([...(account.default_features || []), feature])]
        : (account.default_features || []).filter((value: string) => value !== feature)
      void updateAccount(account, { default_features: defaults })
    }

    function changeRole(account: Dict, role: string) {
      const allowed = ROLE_FEATURES[role] || []
      const features = (account.features || []).filter((feature: string) => allowed.includes(feature))
      const defaults = (account.default_features || []).filter((feature: string) => features.includes(feature))
      void updateAccount(account, { role, features, default_features: defaults })
    }

    onMounted(async () => {
      await loadAccounts()
      timer = window.setInterval(() => {
        if (accounts.value.some(account => account.status === 'checking')) void loadAccounts(false)
      }, 3000)
    })
    onUnmounted(() => window.clearInterval(timer))
    watch(() => props.refreshSeq, () => loadAccounts())

    return () => h('div', { class: 'account-center-page' }, [
      h('section', { class: 'pane global-settings-pane' }, [
        sectionTitle({ title: '系统与授权', subtitle: '软件更新、产品授权和设备信息集中管理', icon: Key, tone: 'blue' }),
        h('div', { class: 'global-settings-grid' }, [
          h('article', { class: 'global-setting-card' }, [
            h('div', { class: 'global-setting-head' }, [
              h('div', [h(Key), h('strong', '授权与设备')]),
              h('i', { class: ['global-settings-dot', licenseStateClass(props.licenseInfo)], 'aria-label': licenseStatusText(props.licenseInfo) }),
            ]),
            h('p', licenseStatusText(props.licenseInfo)),
            h('small', `设备码：${props.licenseInfo.device_code || '尚未生成'}`),
            h('button', { class: 'primary-soft', onClick: () => emit('open-license') }, '管理授权与设备'),
          ]),
          h('article', { class: 'global-setting-card' }, [
            h('div', { class: 'global-setting-head' }, [h('div', [h(Refresh), h('strong', '软件更新')])]),
            h('p', props.appVersion ? `当前版本 v${props.appVersion}` : '正在读取当前版本'),
            h('small', props.appPackaged ? '检查并安装已发布的新版本' : '开发环境仅验证入口，正式包执行更新'),
            h('button', { class: 'primary-soft', disabled: props.updateChecking, onClick: () => emit('check-update') }, props.updateChecking ? '正在检查...' : '检查更新'),
          ]),
        ]),
      ]),
      h('section', { class: 'pane account-create-pane' }, [
        sectionTitle({ title: '统一账号中心', subtitle: '一个账号对应一个独立登录态，所有工作台从这里选择', icon: User, tone: 'teal', aside: h('button', { class: 'secondary-action', onClick: () => loadAccounts() }, [h(Refresh, { class: 'inline-icon' }), '刷新']) }),
        h('div', { class: 'account-create-row' }, [
          h('select', { value: draft.value.platform, onChange: (event: Event) => draft.value.platform = (event.target as HTMLSelectElement).value }, [
            h('option', { value: 'dy' }, '抖音'), h('option', { value: 'xhs' }, '小红书'), h('option', { value: 'ks' }, '快手'),
          ]),
          h('select', { value: draft.value.role, onChange: (event: Event) => draft.value.role = (event.target as HTMLSelectElement).value }, Object.keys(ROLE_FEATURES).map(role => h('option', { value: role }, roleLabel(role)))),
          h('input', { value: draft.value.name, placeholder: '账号名称，例如：品牌主账号', onInput: (event: Event) => draft.value.name = (event.target as HTMLInputElement).value }),
          h('button', { class: 'primary-action', disabled: loading.value, onClick: createAccount }, [h(Plus, { class: 'inline-icon' }), loading.value ? '创建中...' : '添加账号']),
        ]),
        h('p', { class: 'muted-text' }, '品牌账号只允许私信和内容发布；拓客、引流等高风险操作请使用运营号、引流号或测试号。'),
      ]),
      accounts.value.length
        ? h('div', { class: 'account-center-grid' }, accounts.value.map(renderAccount))
        : emptyState({ title: '还没有平台账号', description: '先添加账号，再扫码登录并分配用途', icon: User }),
    ])

    function renderAccount(account: Dict) {
      const allowed = ROLE_FEATURES[String(account.role)] || []
      return h('article', { class: 'pane account-center-card' }, [
        h('div', { class: 'account-card-head' }, [
          h('div', [h('strong', `${platformLabel(account.platform)} · ${account.name}`)]),
          h('span', { class: `status-pill status-${account.status}` }, statusLabel(account.status)),
        ]),
        account.qrcode_url ? h('img', { class: 'publish-qrcode', src: `${account.qrcode_url}?t=${Date.now()}`, alt: '登录二维码' }) : null,
        h('div', { class: 'account-identity-row' }, [
          h('input', { value: account.name || '', placeholder: '账号名称', onChange: (event: Event) => updateAccount(account, { name: (event.target as HTMLInputElement).value.trim() }) }),
          h('input', { value: account.platform_user_id || '', placeholder: '平台账号ID（可选，用于防止重复添加）', onChange: (event: Event) => updateAccount(account, { platform_user_id: (event.target as HTMLInputElement).value.trim() }) }),
          h('select', { value: account.role, onChange: (event: Event) => changeRole(account, (event.target as HTMLSelectElement).value) }, Object.keys(ROLE_FEATURES).map(role => h('option', { value: role }, roleLabel(role)))),
          h('label', [h('input', { type: 'checkbox', checked: account.enabled, onChange: (event: Event) => updateAccount(account, { enabled: (event.target as HTMLInputElement).checked }) }), '启用账号']),
        ]),
        h('div', { class: 'account-feature-list' }, FEATURES.map(feature => h('div', { class: ['account-feature-row', allowed.includes(feature) ? '' : 'is-disabled'] }, [
          h('label', [h('input', { type: 'checkbox', disabled: !allowed.includes(feature), checked: account.features?.includes(feature), onChange: (event: Event) => toggleFeature(account, feature, (event.target as HTMLInputElement).checked) }), featureLabel(feature)]),
          h('label', [h('input', { type: 'checkbox', disabled: !account.features?.includes(feature), checked: account.default_features?.includes(feature), onChange: (event: Event) => toggleDefault(account, feature, (event.target as HTMLInputElement).checked) }), '设为默认']),
          h('small', featureStatusLabel(account.feature_status?.[feature]?.status)),
        ]))),
        account.last_error ? h('p', { class: 'content-job-error' }, String(account.last_error)) : h('small', `最近检查：${account.last_checked_at || '尚未检查'}`),
        h('div', { class: 'task-card-actions' }, [
          h('button', { class: 'primary-soft', disabled: account.status === 'checking', onClick: () => accountAction(account, 'login') }, account.status === 'checking' ? '处理中...' : account.status === 'ready' ? '重新登录' : '扫码登录'),
          h('button', { class: 'text-icon-button', disabled: account.status === 'checking', onClick: () => accountAction(account, 'check') }, '检查状态'),
          h('button', { class: 'text-icon-button danger', onClick: () => deleteAccount(account) }, '移除'),
        ]),
      ])
    }
  },
})

function platformLabel(value: string) { return ({ dy: '抖音', xhs: '小红书', ks: '快手' } as Dict)[value] || value }
function roleLabel(value: string) { return ({ brand: '品牌号', service: '客服号', operations: '运营号', traffic: '引流号', test: '测试号' } as Dict)[value] || value }
function featureLabel(value: string) { return ({ acquisition: '拓客', message: '私信', traffic: '引流', publish: '内容发布' } as Dict)[value] || value }
function statusLabel(value: string) { return ({ login_required: '待登录', checking: '检查中', ready: '已登录', expired: '已失效', error: '异常' } as Dict)[value] || value }
function featureStatusLabel(value: string) { return ({ ready: '可用', checking: '检查中', expired: '失效', error: '异常', unknown: '未检查' } as Dict)[value] || '未检查' }
function licenseStatusText(info: Dict) { return info.authorized ? '授权有效' : info.status === 'failed' ? '未授权' : '等待授权' }
function licenseStateClass(info: Dict) { return info.authorized ? 'is-authorized' : info.status === 'failed' ? 'is-denied' : 'is-pending' }
