import { defineComponent, h, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'

import { api } from '../../shared/api'
import type { Dict } from '../../shared/types'


export const SoftwareUpdateDialog = defineComponent({
  name: 'SoftwareUpdateDialog',
  props: { open: { type: Boolean, required: true } },
  emits: ['close', 'busy', 'installing'],
  setup(props, { emit }) {
    const status = ref<Dict>({ phase: 'idle', percent: 0 })
    const pending = ref(false)
    const confirming = ref(false)
    const error = ref('')
    let generation = 0
    let timer: ReturnType<typeof setTimeout> | undefined

    function stopPolling() {
      if (timer !== undefined) clearTimeout(timer)
      timer = undefined
    }

    function emitBusy() {
      emit('busy', props.open && (pending.value || confirming.value || (!error.value && ['checking', 'downloading', 'installing'].includes(status.value.phase))))
    }

    async function request(method: 'get' | 'post', path: string) {
      if (!props.open || pending.value) return false
      const current = generation
      stopPolling()
      pending.value = true
      error.value = ''
      emitBusy()
      try {
        const { data } = await api[method](path)
        // 关闭或重新打开后，旧请求不能改变新弹窗的状态。
        if (current !== generation || !props.open) return false
        status.value = { ...status.value, ...data }
        return true
      } catch (failure: any) {
        if (current === generation && props.open) error.value = failure?.response?.data?.detail || '更新操作失败，请重新检查'
        return false
      } finally {
        if (current === generation && props.open) {
          pending.value = false
          emitBusy()
          if (!error.value && ['checking', 'downloading'].includes(status.value.phase)) {
            // 串行轮询，不让慢请求与下一轮重叠。
            timer = setTimeout(() => void request('get', '/system/update-status'), 1000)
          }
        }
      }
    }

    async function install() {
      if (pending.value || confirming.value || status.value.phase !== 'ready') return
      const current = generation
      confirming.value = true
      emitBusy()
      try {
        await ElMessageBox.confirm('安装会安全停止任务与私信接收，然后重启软件。确认现在安装？', '安装更新', {
          confirmButtonText: '安装并重启', cancelButtonText: '取消', type: 'warning',
        })
        if (current !== generation || !props.open) return
        if (await request('post', '/system/install-update')) {
          status.value.phase = 'installing'
          emit('installing')
        }
      } catch (failure: any) {
        if (failure !== 'cancel' && failure !== 'close' && current === generation && props.open) error.value = '安装确认失败，请重试'
      } finally {
        if (current === generation) {
          confirming.value = false
          emitBusy()
        }
      }
    }

    watch(() => props.open, open => {
      generation += 1
      stopPolling()
      pending.value = false
      confirming.value = false
      error.value = ''
      if (open) {
        status.value = { phase: 'checking', percent: 0, message: '正在检查更新' }
        void request('post', '/system/check-update')
      } else emitBusy()
    }, { immediate: true })

    onBeforeUnmount(() => {
      generation += 1
      stopPolling()
      emit('busy', false)
    })

    return () => {
      if (!props.open) return null
      const value = status.value
      const percent = Math.max(0, Math.min(100, Number(value.percent) || 0))
      const busy = pending.value || confirming.value || value.phase === 'installing'
      const message = error.value || value.message || value.reason || (value.available ? '发现可用更新' : '当前已是最新版本')
      return h('div', { class: 'license-modal-backdrop', onClick: () => emit('close') }, [
        h('div', {
          class: 'license-modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': '软件更新',
          onClick: (event: Event) => event.stopPropagation(),
          onKeydown: (event: KeyboardEvent) => { if (event.key === 'Escape') emit('close') },
        }, [
          h('div', { class: 'license-modal-head' }, [
            h('div', [h('h3', '软件更新'), h('p', '检查和下载时可继续使用，确认安装后才会重启')]),
            h('button', { type: 'button', class: 'icon-button', 'aria-label': '关闭更新窗口', onClick: () => emit('close') }, '×'),
          ]),
          h('div', { class: 'license-form' }, [
            value.current_version ? h('p', `当前版本：${value.current_version}`) : null,
            value.version ? h('p', `更新版本：${value.version}${value.size ? ` · ${(Number(value.size) / 1024 / 1024).toFixed(1)} MB` : ''}`) : null,
            value.notes ? h('label', [h('span', '更新说明'), h('p', { style: { whiteSpace: 'pre-wrap' } }, String(value.notes))]) : null,
            h('div', { class: ['license-status-card', !error.value && value.phase !== 'failed' ? 'authorized' : ''], role: 'status' }, [
              h('span', message),
              value.reason && value.reason !== message ? h('small', String(value.reason)) : null,
              value.phase === 'downloading' ? h('label', [
                h('span', `下载与校验：${percent}%`),
                h('progress', { value: percent, max: 100, 'aria-label': '更新下载进度', style: { width: '100%' } }),
              ]) : null,
            ]),
            h('div', { class: 'license-actions' }, [
              h('button', { type: 'button', class: 'secondary-action', disabled: busy, onClick: () => void request('post', '/system/check-update') }, error.value || value.phase === 'failed' ? '重新检查' : '检查更新'),
              value.phase === 'ready'
                ? h('button', { type: 'button', class: 'primary-action', disabled: busy, onClick: install }, '安装并重启')
                : value.can_prepare && !['checking', 'downloading', 'installing'].includes(value.phase)
                  ? h('button', { type: 'button', class: 'primary-action', disabled: busy, onClick: () => void request('post', '/system/prepare-update') }, '下载更新')
                  : null,
            ]),
          ]),
        ]),
      ])
    }
  },
})
