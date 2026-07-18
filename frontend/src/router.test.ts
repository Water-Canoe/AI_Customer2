import { describe, expect, it } from 'vitest'

import { assetMatchesSegment, filterMaterialAssets, paginateMaterialAssets } from './pages/ContentWorkbenchPage'
import { paginateBackups } from './pages/SettingsPage'
import {
  automationPlanTypes,
  movePlan,
  normalizeTrafficConfig,
  trafficSourceOptions,
} from './pages/AutomationPlanPage'
import { routes } from './router'


describe('工作台路由', () => {
  it('提供独立的自动化中心页面', () => {
    const automationRoute = routes.find(route => route.name === 'automation-plans')

    expect(automationRoute?.path).toBe('/automation-plans')
    expect(typeof automationRoute?.component).toBe('function')
  })

  it('统一运行队列作为独立全局页面', () => {
    const runtimeRoute = routes.find(route => route.name === 'runtime-center')
    const logsRoute = routes.find(route => route.name === 'logs')

    expect(runtimeRoute?.path).toBe('/runtime')
    expect(runtimeRoute?.meta?.title).toBe('运行中心')
    expect(logsRoute?.meta?.title).toBe('采集记录')
  })

  it('私信工作台拥有独立设置页', () => {
    const settingsRoute = routes.find(route => route.name === 'message-settings')

    expect(settingsRoute?.path).toBe('/message-settings')
    expect(settingsRoute?.meta?.title).toBe('私信设置')
  })

  it('全局设置统一承载更新、授权和账号入口', () => {
    const globalSettingsRoute = routes.find(route => route.name === 'global-settings')

    expect(globalSettingsRoute?.path).toBe('/global-settings')
    expect(routes.some(route => route.name === 'accounts')).toBe(false)
  })

  it('三类自动化计划拖动后按目标位置重排', () => {
    const plans = [{ id: 'lead' }, { id: 'message' }, { id: 'traffic' }]

    expect(movePlan(plans, 'traffic', 'lead').map(plan => plan.id)).toEqual(['traffic', 'lead', 'message'])
    expect(plans.map(plan => plan.id)).toEqual(['lead', 'message', 'traffic'])
  })

  it('自动化中心只提供三种固定任务卡', () => {
    expect(automationPlanTypes.map(([value]) => value)).toEqual(['keyword_lead', 'message', 'traffic'])
  })

  it('自动引流提供抖音四种来源并归一化快手限制', () => {
    expect(trafficSourceOptions.map(([value]) => value)).toEqual([
      'random_feed',
      'competitor_videos',
      'collected_keyword',
      'search_keyword',
    ])
    expect(normalizeTrafficConfig({
      platform: 'ks',
      source_mode: 'search_keyword',
      source_value: 'AI客服',
      action_comment_image: true,
      action_like: true,
    })).toMatchObject({
      platform: 'ks',
      source_mode: 'random_feed',
      source_value: '',
      action_comment_image: false,
      action_like: true,
    })
    expect(normalizeTrafficConfig({ platform: 'dy', source_mode: 'search_keyword', source_value: 'AI客服' }))
      .toMatchObject({ platform: 'dy', source_mode: 'search_keyword', source_value: 'AI客服' })
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
    const sharedContentLoader = contentRoutes.find(route => route.name === 'content-create')?.component
    expect(contentRoutes.filter(route => route.name !== 'content-publish').every(route => route.component === sharedContentLoader)).toBe(true)
    expect(contentRoutes.find(route => route.name === 'content-publish')?.component).not.toBe(sharedContentLoader)
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

  it('备份列表分页并在删除末页后校正页码', () => {
    const backups = Array.from({ length: 6 }, (_, index) => ({ id: String(index) }))

    expect(paginateBackups(backups, 2, 5).items.map(item => item.id)).toEqual(['5'])
    expect(paginateBackups(backups.slice(0, 5), 2, 5).page).toBe(1)
  })
})
