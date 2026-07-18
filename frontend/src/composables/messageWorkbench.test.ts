import { describe, expect, it } from 'vitest'

import { selectDmScript } from './messageWorkbench'


describe('selectDmScript', () => {
  it('returns the configured fixed script when fixed mode is enabled', () => {
    const script = selectDmScript({ dm_script_mode: 'fixed', fixed_dm_script: '  固定问候语  ' }, 'AI问候语')

    expect(script).toMatchObject({ text: '固定问候语', label: '固定话术' })
  })

  it('returns the customer AI script in AI mode', () => {
    const script = selectDmScript({ dm_script_mode: 'ai' }, '  AI问候语  ')

    expect(script).toMatchObject({ text: 'AI问候语', label: 'AI话术' })
  })
})
