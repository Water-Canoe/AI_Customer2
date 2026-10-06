import { computed, defineComponent, h, ref, type PropType } from 'vue'
import type { Dict } from '../../shared/types'
import { ListPagination, paginateItems } from '../ui/ListPagination'

// 搜索只筛选展示数据，选中的资产 ID 始终由创作草稿持有。
export default defineComponent({
  name: 'ContentAssetPicker',
  props: {
    assets: { type: Array as PropType<Dict[]>, required: true },
    selectedIds: { type: Array as PropType<string[]>, required: true },
    busy: { type: Boolean, default: false },
  },
  emits: ['update:selectedIds', 'import', 'close'],
  setup(props, { emit }) {
    const search = ref('')
    const kind = ref('all')
    const page = ref(1)
    const items = computed(() => props.assets.filter(asset => {
      return ['video', 'image'].includes(asset.asset_type)
        && (kind.value === 'all' || asset.asset_type === kind.value)
        && String(asset.name || '').toLowerCase().includes(search.value.trim().toLowerCase())
    }))
    const pagination = computed(() => paginateItems(items.value, page.value, 12))
    function toggle(id: string) {
      emit('update:selectedIds', props.selectedIds.includes(id)
        ? props.selectedIds.filter(value => value !== id)
        : [...props.selectedIds, id])
    }
    return () => h('div', { class: 'content-picker-backdrop' }, [
      h('section', { class: 'content-picker workspace-panel', role: 'dialog', 'aria-modal': true, 'aria-label': '选择本地素材' }, [
        h('div', { class: 'workspace-heading' }, [
          h('div', [h('h2', '选择本地素材'), h('p', { class: 'workspace-note' }, `已选 ${props.selectedIds.length} 项 · 选择会跨搜索和翻页保留`)]),
          h('button', { class: 'secondary-action', onClick: () => emit('close') }, '完成选择'),
        ]),
        h('div', { class: 'workspace-toolbar' }, [
          h('input', { placeholder: '搜索素材名称', value: search.value, onInput: (event: Event) => { search.value = (event.target as HTMLInputElement).value; page.value = 1 } }),
          h('select', { value: kind.value, onChange: (event: Event) => { kind.value = (event.target as HTMLSelectElement).value; page.value = 1 } }, [
            h('option', { value: 'all' }, '视频和图片'), h('option', { value: 'video' }, '视频'), h('option', { value: 'image' }, '图片'),
          ]),
          h('label', { class: 'primary-action content-import-button' }, [
            props.busy ? '导入中…' : '导入素材',
            h('input', { type: 'file', accept: 'video/*,image/*', multiple: true, hidden: true, disabled: props.busy, onChange: (event: Event) => emit('import', event) }),
          ]),
        ]),
        h('div', { class: 'content-picker-grid' }, pagination.value.items.map(asset => h('button', {
          class: ['content-picker-card', props.selectedIds.includes(asset.id) ? 'is-selected' : ''],
          'aria-pressed': props.selectedIds.includes(asset.id),
          onClick: () => toggle(String(asset.id)),
        }, [
          asset.thumbnail_url ? h('img', { src: asset.thumbnail_url, alt: '' }) : h('span', { class: 'content-picker-placeholder' }, asset.asset_type === 'video' ? '视频' : '图片'),
          h('strong', String(asset.name)),
          h('small', `${asset.width || '—'} × ${asset.height || '—'}${asset.duration ? ` · ${Math.round(asset.duration)} 秒` : ''}`),
        ]))),
        !items.value.length ? h('p', { class: 'workspace-note' }, '没有符合搜索条件的素材，可导入新素材或调整搜索。') : null,
        h(ListPagination, { page: pagination.value.page, pageSize: 12, total: pagination.value.total, onChange: (value: { page: number }) => page.value = value.page }),
      ]),
    ])
  },
})
