import type { Dict } from '../../shared/types'

export const PUBLISH_LIMITS = { title: 20, description: 1000, tags: 5, douyinImages: 35, scheduleHours: 2 }

// 按 Unicode 字符计数，与服务端 Python len 的规则保持一致。
export function publishTextLength(value: unknown): number {
  return Array.from(String(value || '').trim()).length
}

export function parsePublishTags(value: unknown): string[] {
  const items = Array.isArray(value) ? value : String(value || '').split(/[,，\s]+/)
  return [...new Set(items.map(item => String(item).trim().replace(/^#+/, '')).filter(Boolean))]
}

// 与服务端约束一致：提示原文超限，绝不通过截断改变用户已确认的文案。
export function validatePublishCopy(copy: Dict, now = Date.now()): string {
  if (!String(copy.title || '').trim()) return '请填写发布标题'
  if (publishTextLength(copy.title) > PUBLISH_LIMITS.title) return `标题最多 ${PUBLISH_LIMITS.title} 字`
  if (publishTextLength(copy.description) > PUBLISH_LIMITS.description) return `正文最多 ${PUBLISH_LIMITS.description} 字`
  if (parsePublishTags(copy.tags).length > PUBLISH_LIMITS.tags) return `标签最多 ${PUBLISH_LIMITS.tags} 个`
  if (copy.publish_strategy === 'scheduled') {
    const scheduled = new Date(String(copy.scheduled_at || '')).getTime()
    if (!Number.isFinite(scheduled) || scheduled <= now + PUBLISH_LIMITS.scheduleHours * 3600000) return '定时发布时间必须晚于当前时间至少 2 小时'
  }
  return ''
}

export function publishSourceKey(source: Dict): string {
  const ids = source.type === 'video_output' ? source.output_ids : source.asset_ids
  return `${source.type}:${[...(ids || [])].sort().join(',')}`
}

export function buildPublishRequest(composer: Dict): Dict {
  const overrides: Dict = {}
  for (const id of composer.account_ids) {
    const copy = composer.account_overrides[id] || composer
    overrides[id] = {
      title: copy.title, description: copy.description, tags: parsePublishTags(copy.tags),
      publish_strategy: copy.publish_strategy, scheduled_at: copy.scheduled_at, options: copy.options || {},
    }
  }
  return {
    source: composer.source,
    account_ids: composer.account_ids,
    title: composer.title,
    description: composer.description,
    tags: parsePublishTags(composer.tags),
    publish_strategy: composer.publish_strategy,
    scheduled_at: composer.scheduled_at,
    account_overrides: overrides,
  }
}
