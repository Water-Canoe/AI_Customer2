export type AutoSyncReason = 'auto' | 'route' | 'visible'

const DRAFT_PROTECTED_VIEWS = new Set([
  'settings',
  'message-settings',
  'traffic-settings',
  'content-settings',
  'global-settings',
])

type AutoSyncOptions = {
  sync: (reason: AutoSyncReason) => Promise<unknown>
  interval: () => number
  tickMs?: number
}

export function shouldAutoSyncView(view: string, reason: AutoSyncReason) {
  return reason === 'route' || !DRAFT_PROTECTED_VIEWS.has(view)
}

export function shouldReplaceDraft(dirty: boolean, saving: boolean, requestRevision: number, currentRevision: number) {
  return !dirty && !saving && requestRevision === currentRevision
}


export function createAutoSyncController(options: AutoSyncOptions) {
  let timer: number | null = null
  let running = false
  let pendingRoute = false
  let lastSyncAt = 0

  async function trigger(reason: AutoSyncReason) {
    if (running || document.hidden) {
      // 路由切换合并为一次补刷新，普通轮询继续跳过，避免请求叠加。
      if (reason === 'route') pendingRoute = true
      return false
    }
    if (reason === 'auto' && Date.now() - lastSyncAt < options.interval()) return false
    if (reason === 'route') pendingRoute = false
    running = true
    try {
      await options.sync(reason)
      return true
    } finally {
      lastSyncAt = Date.now()
      running = false
      if (pendingRoute) {
        pendingRoute = false
        await trigger('route')
      }
    }
  }

  function handleVisibilityChange() {
    if (!document.hidden) void trigger(pendingRoute ? 'route' : 'visible')
  }

  function start() {
    if (timer) return
    timer = window.setInterval(() => void trigger('auto'), options.tickMs ?? 1000)
    document.addEventListener('visibilitychange', handleVisibilityChange)
  }

  function stop() {
    if (timer) window.clearInterval(timer)
    timer = null
    pendingRoute = false
    document.removeEventListener('visibilitychange', handleVisibilityChange)
  }

  function markSynced() {
    lastSyncAt = Date.now()
  }

  return { markSynced, start, stop, trigger }
}
