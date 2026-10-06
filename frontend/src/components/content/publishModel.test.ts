import { describe, expect, it } from 'vitest'
import { buildPublishRequest, validatePublishCopy } from './publishModel'

describe('发布文案与稳定来源', () => {
  it('与服务端一致按字符计算标题和正文，并在原文超限或定时过早时拒绝', () => {
    const now = Date.parse('2026-10-06T00:00:00Z')
    const copy = { title: '😀'.repeat(20), description: '字'.repeat(1000), tags: '#一, 二, 三, 四, 五', publish_strategy: 'immediate' }
    expect(validatePublishCopy(copy, now)).toBe('')
    expect(validatePublishCopy({ ...copy, title: copy.title + '字' }, now)).toContain('标题最多')
    expect(validatePublishCopy({ ...copy, description: copy.description + '字' }, now)).toContain('正文最多')
    expect(validatePublishCopy({ ...copy, tags: copy.tags + ', 六' }, now)).toContain('标签最多')
    expect(validatePublishCopy({ ...copy, publish_strategy: 'scheduled', scheduled_at: '2026-10-06T02:00:00Z' }, now)).toContain('至少 2 小时')
    expect(validatePublishCopy({ ...copy, publish_strategy: 'scheduled', scheduled_at: '2026-10-06T02:01:00Z' }, now)).toBe('')
    expect(copy.title).toBe('😀'.repeat(20))
  })

  it('账号覆盖只提交文案字段，保留稳定成品 ID 和供应商选项', () => {
    const request = buildPublishRequest({ source: { type: 'video_output', output_ids: ['old-output', 'new-output'] }, account_ids: ['dy', 'xhs'], title: '统一标题', description: '统一正文', tags: '#原始, 原始', publish_strategy: 'immediate', scheduled_at: '', account_overrides: { xhs: { title: '小红书标题', description: '独立正文', tags: ['#独立'], publish_strategy: 'scheduled', scheduled_at: '2026-10-08T12:00', options: { thumbnail_asset_id: 'cover' } } } })
    expect(request.source).toEqual({ type: 'video_output', output_ids: ['old-output', 'new-output'] })
    expect(request.account_overrides.dy).toEqual({ title: '统一标题', description: '统一正文', tags: ['原始'], publish_strategy: 'immediate', scheduled_at: '', options: {} })
    expect(request.account_overrides.xhs.options).toEqual({ thumbnail_asset_id: 'cover' })
    expect(request.account_overrides.xhs.title).toBe('小红书标题')
    expect(request.account_overrides.dy).not.toHaveProperty('source')
    expect(request.account_overrides.dy).not.toHaveProperty('account_overrides')
  })
})
