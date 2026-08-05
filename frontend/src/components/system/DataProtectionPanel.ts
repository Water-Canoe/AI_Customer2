import { defineComponent, h, onMounted, ref, watch, type PropType } from 'vue'
import { DataAnalysis, Delete, Refresh, Warning } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { api } from '../../shared/api'
import type { Dict } from '../../shared/types'
import { ListPagination, paginateItems, type PageChange } from '../ui/ListPagination'
import { sectionTitle } from '../ui/Workbench'


const BACKUP_PAGE_SIZE = 5


export const DataProtectionPanel = defineComponent({
  name: 'DataProtectionPanel',
  props: {
    tombstoneSummary: { type: Object as PropType<Dict>, default: () => ({}) },
    tombstones: { type: Object as PropType<Dict>, default: () => ({ items: [] }) },
    tombstoneFilters: { type: Object as PropType<Dict>, default: () => ({}) },
    refreshSeq: { type: Number, default: 0 },
  },
  emits: ['clear-data', 'load-tombstones'],
  setup(props, { emit }) {
    const backups = ref<Dict>({ items: [], total: 0, schema: {} })
    const loading = ref(false)
    const creating = ref(false)
    const restoring = ref('')
    const deleting = ref('')
    const page = ref(1)

    async function loadBackups() {
      loading.value = true
      try {
        const { data } = await api.get('/system/backups')
        backups.value = data
        page.value = paginateBackups(data.items || [], page.value, BACKUP_PAGE_SIZE).page
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '备份列表加载失败')
      } finally {
        loading.value = false
      }
    }

    async function createBackup() {
      creating.value = true
      try {
        await api.post('/system/backups', { reason: 'manual' })
        ElMessage.success('业务数据库、MyCrawler 原始库和业务文件已备份')
        page.value = 1
        await loadBackups()
      } catch (error: any) {
        ElMessage.error(error?.response?.data?.detail || '创建备份失败')
      } finally {
        creating.value = false
      }
    }

    async function restoreBackup(item: Dict) {
      try {
        const result = await ElMessageBox.prompt(
          '恢复前会自动创建当前数据的安全备份。请输入“恢复备份”继续。',
          '恢复数据备份',
          { inputPlaceholder: '恢复备份', confirmButtonText: '确认恢复', cancelButtonText: '取消', type: 'warning' },
        )
        if (result.value !== '恢复备份') {
          ElMessage.warning('确认文本不正确，未执行恢复')
          return
        }
        restoring.value = String(item.id || '')
        await api.post(`/system/backups/${encodeURIComponent(String(item.id || ''))}/restore`, { confirm: result.value })
        ElMessage.success('备份已恢复，页面即将重新加载')
        window.setTimeout(() => window.location.reload(), 500)
      } catch (error: any) {
        if (error === 'cancel' || error?.toString?.() === 'cancel') return
        ElMessage.error(error?.response?.data?.detail || '恢复备份失败')
      } finally {
        restoring.value = ''
      }
    }

    async function deleteBackup(item: Dict) {
      const backupId = String(item.id || '')
      if (!backupId) return
      try {
        await ElMessageBox.confirm('删除后无法恢复，确认删除这份备份？', '删除数据备份', {
          confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning',
        })
        deleting.value = backupId
        await api.delete(`/system/backups/${encodeURIComponent(backupId)}`)
        ElMessage.success('备份已删除')
        await loadBackups()
      } catch (error: any) {
        if (error === 'cancel' || error?.toString?.() === 'cancel') return
        ElMessage.error(error?.response?.data?.detail || '删除备份失败')
      } finally {
        deleting.value = ''
      }
    }

    onMounted(loadBackups)
    watch(() => props.refreshSeq, () => loadBackups())

    return () => h('div', { class: 'data-protection-page' }, [
      h('section', { class: 'pane data-protection-pane' }, [
        sectionTitle({
          title: '备份与恢复',
          subtitle: `数据库版本 ${backups.value.schema?.current || 0}/${backups.value.schema?.latest || 0}`,
          icon: DataAnalysis,
          tone: 'blue',
          aside: h('button', { class: 'secondary-action', disabled: loading.value, onClick: loadBackups }, [h(Refresh, { class: 'inline-icon' }), loading.value ? '刷新中...' : '刷新']),
        }),
        h('div', { class: 'data-lifecycle-actions' }, [
          h('button', { class: 'primary-action', disabled: creating.value, onClick: createBackup }, creating.value ? '备份中...' : '立即备份'),
        ]),
        renderBackups(backups.value, page.value, value => { page.value = value }, loading.value, restoring.value, deleting.value, restoreBackup, deleteBackup),
      ]),
      h('section', { class: 'pane data-protection-pane' }, [
        renderTombstones(
          props.tombstoneSummary,
          props.tombstones,
          props.tombstoneFilters,
          filters => emit('load-tombstones', filters),
        ),
      ]),
      h('section', { class: 'pane danger-zone' }, [
        sectionTitle({ title: '危险操作', subtitle: '执行前自动创建完整备份', icon: Warning, tone: 'red' }),
        h('p', '清空项目库和原始采集库中的业务记录；配置、登录状态、本地素材和历史备份保留。'),
        h('button', { class: 'wide-action danger-action', onClick: () => emit('clear-data') }, [h(Delete, { class: 'inline-icon' }), '清空业务记录']),
      ]),
    ])
  },
})


function renderBackups(
  value: Dict,
  requestedPage: number,
  changePage: (page: number) => void,
  loading: boolean,
  restoring: string,
  deleting: string,
  restore: (item: Dict) => void,
  remove: (item: Dict) => void,
) {
  const backups = paginateBackups(value.items || [], requestedPage, BACKUP_PAGE_SIZE)
  return [
    backups.items.length
      ? h('div', { class: 'backup-list' }, backups.items.map((item: Dict) => h('article', [
          h('div', [
            h('strong', item.created_at || item.id),
            h('span', `项目库 ${formatFileSize(item.database_size)} · 原始库 ${item.media_crawler_database ? formatFileSize(item.media_crawler_database.size) : '未初始化'} · 文件 ${formatFileSize(item.data_size)} / ${item.file_count || 0} 个`),
          ]),
          h('small', backupReasonLabel(item.reason)),
          h('div', { class: 'backup-item-actions' }, [
            h('button', { class: 'text-icon-button', disabled: Boolean(restoring) || Boolean(deleting), onClick: () => restore(item) }, restoring === String(item.id || '') ? '恢复中...' : '恢复'),
            h('button', { class: 'text-icon-button danger', disabled: Boolean(restoring) || Boolean(deleting), onClick: () => remove(item) }, deleting === String(item.id || '') ? '删除中...' : '删除'),
          ]),
        ])))
      : h('div', { class: 'diagnostic-empty' }, loading ? '正在读取备份...' : '暂无可恢复备份'),
    h(ListPagination, {
      page: backups.page,
      pageSize: BACKUP_PAGE_SIZE,
      total: (value.items || []).length,
      compact: true,
      onChange: (payload: PageChange) => changePage(payload.page),
    }),
  ]
}


function renderTombstones(summary: Dict, tombstones: Dict, filters: Dict, load: (filters: Dict) => void) {
  const items = tombstones.items || []
  return [
    sectionTitle({ title: '防重复记录', subtitle: `共 ${summary.total || 0} 条`, icon: Delete, tone: 'amber' }),
    h('div', { class: 'quality-summary tombstone-summary' }, [
      qualityMetric('账号', summary.accounts || 0),
      qualityMetric('内容', summary.contents || 0),
      qualityMetric('评论', summary.comments || 0),
    ]),
    h('div', { class: 'tombstone-filters' }, [
      h('select', {
        value: filters.entity_type || '',
        onChange: (event: Event) => load({ entity_type: (event.target as HTMLSelectElement).value, page: 1 }),
      }, [
        h('option', { value: '' }, '全部类型'),
        h('option', { value: 'author_account' }, '账号'),
        h('option', { value: 'content' }, '内容'),
        h('option', { value: 'comment' }, '评论'),
      ]),
      h('input', {
        value: filters.query || '',
        placeholder: '搜索标识、来源或快照',
        onInput: (event: Event) => load({ query: (event.target as HTMLInputElement).value, page: 1 }),
      }),
    ]),
    items.length ? h('div', { class: 'tombstone-list' }, items.map((item: Dict) => h('article', [
      h('div', [
        h('strong', `${entityTypeLabel(item.entity_type)} · ${item.platform || '-'}`),
        h('small', `${item.identifier_type || '-'}: ${item.identifier_value || '-'}`),
      ]),
      h('p', item.snapshot_summary || '无快照摘要'),
      h('small', `${item.source || '未标记来源'} · ${item.updated_at || item.created_at || ''}`),
    ]))) : h('div', { class: 'diagnostic-empty' }, '当前没有防重复记录'),
    h(ListPagination, {
      page: Number(tombstones.page || 1),
      pageSize: Number(tombstones.page_size || filters.page_size || 20),
      total: Number(tombstones.total || 0),
      onChange: (payload: PageChange) => load({ page: payload.page, page_size: payload.page_size }),
    }),
  ]
}


export function paginateBackups(items: Dict[], page: number, pageSize: number) {
  const result = paginateItems(items, page, pageSize)
  return { items: result.items, page: result.page, totalPages: result.totalPages }
}


function formatFileSize(value: unknown) {
  const bytes = Number(value || 0)
  if (bytes <= 0) return '0 KB'
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}


function backupReasonLabel(value: unknown) {
  const reason = String(value || 'manual')
  if (reason === 'manual') return '手动备份'
  if (reason === 'pre_clear_all_data') return '清空数据前自动备份'
  if (reason.startsWith('pre_restore_')) return '恢复前安全备份'
  if (reason.startsWith('pre_migration_')) return '数据库升级前自动备份'
  return reason
}


function qualityMetric(label: string, value: number) {
  return h('div', [h('span', label), h('strong', String(value))])
}


function entityTypeLabel(type: string) {
  return ({ author_account: '账号', content: '内容', comment: '评论' } as Record<string, string>)[type] || type || '-'
}
