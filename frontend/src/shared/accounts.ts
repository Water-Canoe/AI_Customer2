import type { Dict } from './types'

// 工作台只读取对应功能的登录态，不能用创作者登录态代替用户登录态。
export function isAccountFeatureReady(account: Dict, feature: string) {
  return Boolean(account.enabled && account.features?.includes(feature) && account.feature_status?.[feature]?.status === 'ready')
}
