export type Dict = Record<string, any>

export type Platform = 'dy' | 'xhs' | 'ks'
export type TaskMode = 'competitor_discovery' | 'competitor_crawl' | 'demand_content' | 'own_account' | 'profile_enrichment' | 'account_analysis'
export type TaskStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export type AccountFeature = 'acquisition' | 'message' | 'traffic' | 'publish'
export type AccountRole = 'brand' | 'service' | 'operations' | 'traffic' | 'test'
export type RuntimeJobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'

export interface PageResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages?: number
}

export interface TaskRecord {
  id: string
  name: string
  mode: TaskMode
  platform: Platform
  login_type: 'qrcode' | 'phone' | 'cookie'
  crawler_type: string
  keywords: string
  specified_id: string
  creator_id: string
  content_count: number
  comment_count: number
  collect_content: boolean
  collect_comments: boolean
  collect_authors: boolean
  collect_sub_comments: boolean
  max_concurrency: number
  tcp_mode: boolean
  headless: boolean
  execute_crawler: boolean
  status: TaskStatus
  archived: boolean
  error: string
  process_id: number | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
  automation_managed: boolean
  account_id: string
  outcome: Dict
  logs?: Dict[]
  runtime_job?: RuntimeJob
}

export type TaskPage = PageResponse<TaskRecord> & { total_pages: number }

export interface AccountFeatureStatus {
  feature?: AccountFeature
  is_default?: boolean | number
  status: string
  last_checked_at?: string | null
  last_error?: string
}

export interface PlatformAccount {
  id: string
  platform: Platform
  name: string
  status: string
  is_default: boolean
  enabled: boolean
  last_checked_at: string | null
  last_error: string
  qrcode_url: string
  deleted_at: string | null
  created_at: string
  updated_at: string
  role: AccountRole
  platform_user_id: string
  features: string[]
  default_features: string[]
  feature_status: Record<string, AccountFeatureStatus | undefined>
  login_status: Record<string, { available: boolean, status: string, last_checked_at: string | null, last_error: string }>
}

export interface RuntimeJob {
  id: string
  kind: string
  entity_id: string
  resource: string
  payload: Dict
  status: RuntimeJobStatus
  priority: number
  attempt: number
  max_attempts: number
  cancel_requested: boolean
  error: string
  result: Dict
  heartbeat_at: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
}

export type RuntimeJobPage = PageResponse<RuntimeJob> & { active: Record<string, number> }

