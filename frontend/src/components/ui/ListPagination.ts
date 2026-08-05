import { defineComponent, h, type PropType } from 'vue'
import { ElPagination } from 'element-plus'


export const DEFAULT_PAGE_SIZE = 20
export const COMPACT_PAGE_SIZE = 10
export const PAGE_SIZE_OPTIONS = [10, 20, 50]

export type PageChange = { page: number, page_size: number }

export function paginateItems<T>(items: T[], page: number, pageSize: number) {
  const safePageSize = Math.max(1, Number(pageSize) || DEFAULT_PAGE_SIZE)
  const total = items.length
  const totalPages = Math.max(1, Math.ceil(total / safePageSize))
  const currentPage = Math.min(Math.max(1, Number(page) || 1), totalPages)
  const start = (currentPage - 1) * safePageSize
  return {
    items: items.slice(start, start + safePageSize),
    page: currentPage,
    pageSize: safePageSize,
    total,
    totalPages,
    start,
  }
}

export const ListPagination = defineComponent({
  name: 'ListPagination',
  props: {
    page: { type: Number, default: 1 },
    pageSize: { type: Number, default: DEFAULT_PAGE_SIZE },
    total: { type: Number, default: 0 },
    pageSizes: { type: Array as PropType<number[]>, default: () => PAGE_SIZE_OPTIONS },
    compact: { type: Boolean, default: false },
  },
  emits: ['change'],
  setup(props, { emit }) {
    // 所有业务列表通过同一事件格式切页，服务端和前端分页可以共用。
    const change = (page: number, pageSize: number) => emit('change', { page, page_size: pageSize } as PageChange)
    return () => h('div', { class: ['list-pagination', { 'is-compact': props.compact }] }, [
      h(ElPagination, {
        background: true,
        currentPage: props.page,
        pageSize: props.pageSize,
        pageSizes: props.pageSizes,
        total: props.total,
        layout: props.compact ? 'total, prev, pager, next' : 'total, sizes, prev, pager, next, jumper',
        onCurrentChange: (page: number) => change(page, props.pageSize),
        onSizeChange: (pageSize: number) => change(1, pageSize),
      }),
    ])
  },
})
