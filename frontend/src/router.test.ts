import { describe, expect, it } from 'vitest'

import ContentWorkbenchPage, { assetMatchesSegment } from './pages/ContentWorkbenchPage'
import { routes } from './router'


describe('内容工作台路由', () => {
  it('提供四个统一工作台子页面', () => {
    const contentRoutes = routes.filter(route => String(route.name || '').startsWith('content-'))

    expect(contentRoutes.map(route => route.path)).toEqual([
      '/content-create',
      '/content-assets',
      '/content-records',
      '/content-settings',
    ])
    expect(contentRoutes.every(route => route.component === ContentWorkbenchPage)).toBe(true)
    expect(contentRoutes.map(route => route.meta?.title)).toEqual(['视频创作', '内容资产', '生成记录', '内容设置'])
  })

  it('背景音乐列表不会混入克隆参考音频', () => {
    expect(assetMatchesSegment({ asset_type: 'audio', purpose: 'background_music' }, 'background_music')).toBe(true)
    expect(assetMatchesSegment({ asset_type: 'audio', purpose: 'voice_reference' }, 'background_music')).toBe(false)
  })
})
