import { describe, expect, it } from 'vitest'

import { isAccountFeatureReady } from './accounts'

describe('isAccountFeatureReady', () => {
  it('只接受当前功能自己的登录态', () => {
    const account = {
      enabled: true,
      status: 'ready',
      features: ['message', 'publish'],
      feature_status: {
        message: { status: 'expired' },
        publish: { status: 'ready' },
      },
    }

    expect(isAccountFeatureReady(account, 'message')).toBe(false)
    expect(isAccountFeatureReady(account, 'publish')).toBe(true)
  })
})
