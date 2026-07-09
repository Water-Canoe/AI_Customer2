import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createAutoSyncController } from './autoSync'


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
