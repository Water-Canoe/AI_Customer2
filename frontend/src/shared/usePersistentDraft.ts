import { onBeforeUnmount, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api } from './api'
import type { Dict } from './types'

export type DraftKind = 'crawl' | 'video' | 'publish' | 'traffic' | 'automation'
export type DraftStatus = 'idle' | 'loading' | 'dirty' | 'saving' | 'saved' | 'error'
export interface DraftRecord { kind: DraftKind; key: string; payload: Dict; updated_at: string | null; exists: boolean }

// 草稿写入串行化，避免较慢的旧请求覆盖新输入；凭据不得进入 getPayload。
export function usePersistentDraft(options: {
  kind: DraftKind
  key: MaybeRefOrGetter<string>
  getPayload: () => Dict
  applyPayload: (payload: Dict) => void
  delay?: number
}) {
  const status = ref<DraftStatus>('idle')
  const error = ref('')
  const updatedAt = ref<string | null>(null)
  let ready = false
  let restoring = false
  let revision = 0
  let baseline = ''
  let timer: ReturnType<typeof setTimeout> | undefined
  let writes: Promise<unknown> = Promise.resolve()
  const path = () => `/drafts/${options.kind}/${encodeURIComponent(toValue(options.key))}`
  const serialize = () => JSON.stringify(options.getPayload())

  function cancelTimer() { if (timer) clearTimeout(timer); timer = undefined }

  async function load() {
    cancelTimer()
    ready = false
    const startingRevision = revision
    const requestPath = path()
    status.value = 'loading'
    try {
      await writes.catch(() => undefined)
      const { data } = await api.get<DraftRecord>(requestPath)
      if (requestPath !== path()) return data
      // 读取期间已开始输入时保留本地内容。
      if (startingRevision === revision && data.exists) {
        restoring = true
        options.applyPayload(data.payload)
        restoring = false
      }
      ready = true
      baseline = serialize()
      updatedAt.value = data.updated_at
      error.value = ''
      status.value = data.exists ? 'saved' : 'idle'
      if (startingRevision !== revision) { baseline = ''; scheduleSave() }
      return data
    } catch (cause: any) {
      restoring = false
      status.value = 'error'
      error.value = cause?.response?.data?.detail || '草稿读取失败，请重试'
      throw cause
    }
  }

  function save() {
    cancelTimer()
    if (!ready) return writes
    const body = serialize()
    if (body === baseline) return writes
    const requestPath = path()
    const savedRevision = revision
    status.value = 'saving'
    const operation = writes.catch(() => undefined).then(async () => {
      try {
        const { data } = await api.put<DraftRecord>(requestPath, { payload: JSON.parse(body) })
        if (requestPath === path()) {
          baseline = body
          updatedAt.value = data.updated_at
          error.value = ''
          status.value = savedRevision === revision ? 'saved' : 'dirty'
        }
        return data
      } catch (cause: any) {
        status.value = 'error'
        error.value = cause?.response?.data?.detail || '草稿保存失败，请重试'
        throw cause
      }
    })
    writes = operation
    return operation
  }

  function scheduleSave() {
    if (!ready || restoring) return
    cancelTimer()
    status.value = 'dirty'
    timer = setTimeout(() => { void save().catch(() => undefined) }, options.delay ?? 500)
  }

  async function clear() {
    cancelTimer()
    ready = false
    await writes.catch(() => undefined)
    await api.delete(path())
    writes = Promise.resolve()
    baseline = serialize()
    status.value = 'idle'
    updatedAt.value = null
    // 提交后关闭草稿；下次编辑需显式 load，避免清空表单又生成空草稿。
  }

  const stop = watch(serialize, () => {
    if (restoring) return
    revision += 1
    scheduleSave()
  }, { flush: 'sync' })
  onBeforeRouteLeave(async () => {
    try { await save(); return true } catch { return false }
  })
  onBeforeUnmount(() => { cancelTimer(); stop(); void save().catch(() => undefined) })
  return { status, error, updatedAt, load, save, flush: save, clear, scheduleSave }
}

export function draftStatusLabel(status: DraftStatus) {
  return { idle: '尚未修改', loading: '读取草稿中', dirty: '等待保存', saving: '保存中', saved: '草稿已保存', error: '草稿保存异常' }[status]
}
