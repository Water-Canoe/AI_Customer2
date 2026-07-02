export function platformName(platform: string) {
  return ({ dy: '抖音', xhs: '小红书', ks: '快手' } as Record<string, string>)[platform] || platform
}

export function taskModeName(mode: string) {
  return ({
    competitor_discovery: '竞品账号采集',
    competitor_crawl: '竞品账号爬取',
    demand_content: '找需求内容',
    own_account: '自家账号互动',
    profile_enrichment: '账号资料补全',
    account_analysis: '账号分析'
  } as Record<string, string>)[mode] || mode
}

export function competitorStatusLabel(status: string) {
  return status || '未分析'
}

export function competitorStatusClass(status: string) {
  if (status === '竞品') return 'is-competitor'
  if (status === '非竞品') return 'is-not-competitor'
  if (status === '自家账号') return 'is-own-account'
  if (status === '排队分析') return 'is-queued'
  if (status === '正在分析') return 'is-running'
  return 'is-unknown'
}

export function accountRoleLabel(role: string) {
  return ({
    competitor_candidate: '候选竞品',
    competitor: '竞品',
    own_account: '自家账号',
    lead: '线索账号'
  } as Record<string, string>)[role] || ''
}

export function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}
