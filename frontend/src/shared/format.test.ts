import { describe, expect, it } from 'vitest'

import { accountRoleLabel, clamp, competitorStatusClass, platformName, taskModeName } from './format'


describe('shared formatters', () => {
  it('maps platform and task values without hiding unknown values', () => {
    expect(platformName('xhs')).toBe('小红书')
    expect(taskModeName('account_analysis')).toBe('账号分析')
    expect(platformName('custom')).toBe('custom')
  })

  it('formats account state and clamps numeric layout values', () => {
    expect(competitorStatusClass('正在分析')).toBe('is-running')
    expect(accountRoleLabel('own_account')).toBe('自家账号')
    expect(clamp(14, 0, 10)).toBe(10)
  })
})
