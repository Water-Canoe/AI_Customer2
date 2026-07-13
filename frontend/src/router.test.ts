import { describe, expect, it } from 'vitest'

import ContentWorkbenchPage, { assetMatchesSegment, filterMaterialAssets, paginateMaterialAssets } from './pages/ContentWorkbenchPage'
import AutomationPlanPage from './pages/AutomationPlanPage'
import PublishCenterPage from './pages/PublishCenterPage'
import { routes } from './router'


describe('内容工作台路由', () => {
  it('任务管理之后提供独立自动化计划页面', () => {
    const taskIndex = routes.findIndex(route => route.name === 'tasks')
    const automationIndex = routes.findIndex(route => route.name === 'automation-plans')

    expect(automationIndex).toBe(taskIndex + 1)
    expect(routes[automationIndex].path).toBe('/automation-plans')
    expect(routes[automationIndex].component).toBe(AutomationPlanPage)
  })

  it('提供五个内容工作台子页面', () => {
    const contentRoutes = routes.filter(route => String(route.name || '').startsWith('content-'))

    expect(contentRoutes.map(route => route.path)).toEqual([
      '/content-create',
      '/content-assets',
      '/content-records',
      '/content-publish',
      '/content-settings',
    ])
    expect(contentRoutes.filter(route => route.name !== 'content-publish').every(route => route.component === ContentWorkbenchPage)).toBe(true)
    expect(contentRoutes.find(route => route.name === 'content-publish')?.component).toBe(PublishCenterPage)
    expect(contentRoutes.map(route => route.meta?.title)).toEqual(['视频创作', '内容资产', '生成记录', '发布中心', '内容设置'])
  })

  it('背景音乐列表不会混入克隆参考音频', () => {
    expect(assetMatchesSegment({ asset_type: 'audio', purpose: 'background_music' }, 'background_music')).toBe(true)
    expect(assetMatchesSegment({ asset_type: 'audio', purpose: 'voice_reference' }, 'background_music')).toBe(false)
  })

  it('本地素材可以按类型筛选并分页', () => {
    const assets = Array.from({ length: 10 }, (_, index) => ({ id: String(index), asset_type: index < 3 ? 'video' : 'image' }))

    expect(filterMaterialAssets(assets, 'video')).toHaveLength(3)
    expect(filterMaterialAssets(assets, 'image')).toHaveLength(7)
    expect(paginateMaterialAssets(assets, 2, 8).map(asset => asset.id)).toEqual(['8', '9'])
  })
})
