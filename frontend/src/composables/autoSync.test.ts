import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createAutoSyncController, shouldAutoSyncView, shouldReplaceDraft } from './autoSync'


describe('createAutoSyncController', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-10T00:00:00Z'))
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('throttles idle polling but allows route refresh immediately', async () => {
    const sync = vi.fn(async () => undefined)
    const controller = createAutoSyncController({ sync, interval: () => 3000, tickMs: 1000 })
    controller.start()

    await controller.trigger('route')
    await vi.advanceTimersByTimeAsync(2000)
    expect(sync).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1000)
    expect(sync).toHaveBeenCalledTimes(2)
    controller.stop()
  })

  it('refreshes on visibility recovery and removes listeners after stop', async () => {
    const sync = vi.fn(async () => undefined)
    const controller = createAutoSyncController({ sync, interval: () => 12000 })
    controller.start()

    document.dispatchEvent(new Event('visibilitychange'))
    await Promise.resolve()
    expect(sync).toHaveBeenCalledWith('visible')

    controller.stop()
    document.dispatchEvent(new Event('visibilitychange'))
    await Promise.resolve()
    expect(sync).toHaveBeenCalledTimes(1)
  })
})

describe('settings draft protection', () => {
  it('loads settings on entry but does not poll configuration editors', () => {
    for (const view of ['settings', 'message-settings', 'traffic-settings', 'content-settings', 'global-settings']) {
      expect(shouldAutoSyncView(view, 'route')).toBe(true)
      expect(shouldAutoSyncView(view, 'auto')).toBe(false)
      expect(shouldAutoSyncView(view, 'visible')).toBe(false)
    }
    expect(shouldAutoSyncView('traffic-monitor', 'auto')).toBe(true)
  })

  it('rejects stale responses while a draft is dirty or saving', () => {
    expect(shouldReplaceDraft(false, false, 2, 2)).toBe(true)
    expect(shouldReplaceDraft(true, false, 2, 2)).toBe(false)
    expect(shouldReplaceDraft(false, true, 2, 2)).toBe(false)
    expect(shouldReplaceDraft(false, false, 1, 2)).toBe(false)
  })
})
