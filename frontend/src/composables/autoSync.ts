export type AutoSyncReason = 'auto' | 'route' | 'visible'

type AutoSyncOptions = {
  sync: (reason: AutoSyncReason) => Promise<unknown>
  interval: () => number
  tickMs?: number
}


export function createAutoSyncController(options: AutoSyncOptions) {
  let timer: number | null = null
  let running = false
  let lastSyncAt = 0

  async function trigger(reason: AutoSyncReason) {
    if (running || document.hidden) return false
    if (reason === 'auto' && Date.now() - lastSyncAt < options.interval()) return false
    running = true
    try {
      await options.sync(reason)
      return true
    } finally {
      lastSyncAt = Date.now()
      running = false
    }
  }

  function handleVisibilityChange() {
    if (!document.hidden) void trigger('visible')
  }

  function start() {
    if (timer) return
    timer = window.setInterval(() => void trigger('auto'), options.tickMs ?? 1000)
    document.addEventListener('visibilitychange', handleVisibilityChange)
  }

  function stop() {
    if (timer) window.clearInterval(timer)
    timer = null
    document.removeEventListener('visibilitychange', handleVisibilityChange)
  }

  function markSynced() {
    lastSyncAt = Date.now()
  }

  return { markSynced, start, stop, trigger }
}
