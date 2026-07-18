import { defineComponent, h, onMounted, reactive, ref, watch, type PropType } from 'vue'
import { ChatDotRound, Check, Refresh, Timer } from '@element-plus/icons-vue'
import { ElAlert, ElMessage } from 'element-plus'

import { sectionTitle } from '../components/ui/Workbench'
import { api } from '../shared/api'
import type { Dict } from '../shared/types'


const MESSAGE_SETTING_KEYS = [
  'unreplied_reminder_days',
  'dm_script_mode',
  'fixed_dm_script',
  'auto_dm_fill_only',
  'auto_dm_timeout_seconds',
]


export default defineComponent({
  name: 'MessageSettingsPage',
  props: {
    settings: { type: Object as PropType<Dict>, required: true },
    settingsSaveRevision: { type: Number, default: 0 },
    refreshSeq: { type: Number, default: 0 },
  },
  emits: ['save', 'settings-dirty-change'],
  setup(props, { emit }) {
    const local = reactive<Dict>({})
    const limits = ref<Dict>({})
    const limitDraft = reactive({ daily_limit: 100, hourly_limit: 40 })
    const settingsDirty = ref(false)
    const limitSaving = ref(false)

    function syncSettings() {
      if (settingsDirty.value) return
      MESSAGE_SETTING_KEYS.forEach(key => { local[key] = props.settings[key] })
    }

    function markDirty() {
      settingsDirty.value = true
      emit('settings-dirty-change', true)
    }

    function saveSettings() {
      emit('save', Object.fromEntries(MESSAGE_SETTING_KEYS.map(key => [key, local[key]])))
    }

    async function loadLimits(showError = true) {
      try {
        const { data } = await api.get('/automation/message-limits')
        limits.value = data || {}
        limitDraft.daily_limit = Number(data.daily_limit || 100)
        limitDraft.hourly_limit = Number(data.hourly_limit || 40)
      } catch (error: any) {
        if (showError) ElMessage.error(error?.response?.data?.detail || '私信频率额度加载失败')
      }
    }

    async function saveLimits() {
      limitSaving.value = true
      try {
        const { data } = await api.put('/automation/message-limits', limitDraft)
        limits.value = data
        ElMessage.success('私信频率额度已保存')
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '私信频率额度保存失败')
      } finally {
        limitSaving.value = false
      }
    }

    watch(() => props.settings, syncSettings, { immediate: true, deep: true })
    watch(() => props.settingsSaveRevision, revision => {
      if (!revision) return
      settingsDirty.value = false
      emit('settings-dirty-change', false)
      syncSettings()
    })
    watch(() => props.refreshSeq, () => loadLimits(false))
    onMounted(() => loadLimits())

    return () => h('div', { class: 'message-settings-page' }, [
      h('section', { class: 'pane message-settings-pane' }, [
        sectionTitle({
          title: '私信与跟进设置',
          subtitle: settingsDirty.value ? '有未保存修改，自动同步不会覆盖草稿' : '单客户、批量和定时私信共用这些规则',
          icon: ChatDotRound,
          tone: settingsDirty.value ? 'amber' : 'teal',
        }),
        h('div', { class: 'form-grid' }, [
          numberField(local, 'unreplied_reminder_days', '未回复提醒天数', '默认 3 天；填 0 表示不提醒', markDirty),
          numberField(local, 'auto_dm_timeout_seconds', '只填内容等待秒数', '等待人工确认的窗口保留时间', markDirty),
          h('label', [
            '私信话术来源',
            h('select', {
              value: String(local.dm_script_mode || 'ai'),
              onFocus: markDirty,
              onChange: (event: Event) => { local.dm_script_mode = (event.target as HTMLSelectElement).value; markDirty() },
            }, [
              h('option', { value: 'ai' }, '发送客户 AI 话术'),
              h('option', { value: 'fixed' }, '发送统一固定话术'),
            ]),
          ]),
          h('label', { class: 'form-field field-full' }, [
            '固定话术',
            h('textarea', {
              value: String(local.fixed_dm_script || ''),
              rows: 5,
              placeholder: '选择固定话术时，单客户、批量和定时私信都会使用这里的内容。',
              onFocus: markDirty,
              onInput: (event: Event) => { local.fixed_dm_script = (event.target as HTMLTextAreaElement).value; markDirty() },
            }),
          ]),
        ]),
        h('div', { class: 'toggles' }, [
          h('label', [
            h('input', {
              type: 'checkbox',
              checked: Boolean(local.auto_dm_fill_only),
              onFocus: markDirty,
              onChange: (event: Event) => { local.auto_dm_fill_only = (event.target as HTMLInputElement).checked; markDirty() },
            }),
            '自动私信只填内容，不点击发送',
          ]),
        ]),
        h('div', { class: 'action-row' }, [
          h('button', { class: 'primary-action', onClick: saveSettings }, [h(Check, { class: 'inline-icon' }), '保存私信设置']),
        ]),
      ]),
      h('section', { class: 'pane message-limit-pane' }, [
        sectionTitle({
          title: '私信频率额度',
          subtitle: '单客户、批量和定时私信统一占用，失败或结果不明确也会计数',
          icon: Timer,
          tone: 'blue',
          aside: h('button', { class: 'secondary-action', onClick: () => loadLimits() }, [h(Refresh, { class: 'inline-icon' }), '刷新']),
        }),
        h('div', { class: 'message-limit-grid' }, [
          numberControl('每日不同用户上限', limitDraft.daily_limit, 1, 100, value => { limitDraft.daily_limit = value }),
          numberControl('每小时不同用户上限', limitDraft.hourly_limit, 1, 40, value => { limitDraft.hourly_limit = value }),
          limitMetric('本小时已占用', limits.value.configured ? limits.value.used_hour : '未配置', limits.value.configured ? `剩余 ${limits.value.remaining_hour}` : ''),
          limitMetric('今日已占用', limits.value.configured ? limits.value.used_today : '未配置', limits.value.configured ? `剩余 ${limits.value.remaining_today}` : ''),
        ]),
        h(ElAlert, {
          title: limits.value.notice || '这里只统计本软件产生的私信，无法感知平台 App 内手动发送数量。',
          type: limits.value.fill_only ? 'error' : 'warning',
          description: limits.value.fill_only ? '当前已开启“只填内容不发送”，自动私信计划不能启用或运行。' : '',
          showIcon: true,
          closable: false,
        }),
        h('div', { class: 'action-row' }, [
          h('button', { class: 'primary-action', disabled: limitSaving.value, onClick: saveLimits }, limitSaving.value ? '保存中...' : '保存频率额度'),
        ]),
      ]),
    ])
  },
})


function numberField(local: Dict, key: string, label: string, placeholder: string, markDirty: () => void) {
  return h('label', [
    label,
    h('input', {
      type: 'number',
      min: 0,
      value: Number(local[key] || 0),
      onFocus: markDirty,
      onInput: (event: Event) => { local[key] = Number((event.target as HTMLInputElement).value); markDirty() },
    }),
    h('small', placeholder),
  ])
}


function numberControl(label: string, value: number, min: number, max: number, update: (value: number) => void) {
  return h('label', { class: 'message-limit-control' }, [
    h('span', label),
    h('input', {
      type: 'number', min, max, value,
      onInput: (event: Event) => update(Number((event.target as HTMLInputElement).value)),
    }),
  ])
}


function limitMetric(label: string, value: unknown, note: string) {
  return h('div', { class: 'message-limit-metric' }, [h('span', label), h('strong', String(value)), note ? h('small', note) : null])
}
