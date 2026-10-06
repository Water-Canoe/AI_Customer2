import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import AssetPicker from './AssetPicker'
import { ListPagination } from '../ui/ListPagination'

vi.mock('element-plus', () => ({ ElPagination: { render: () => null } }))

describe('创作素材选择', () => {
  it('搜索、分类和翻页只改变可见素材，选择及导入入口始终保留', async () => {
    const assets = Array.from({ length: 14 }, (_, index) => ({ id: 'asset-' + index, name: '素材' + index, asset_type: index === 13 ? 'image' : 'video' }))
    const wrapper = mount(AssetPicker, { props: { assets, selectedIds: ['asset-0'] } })
    await wrapper.get('input[placeholder="搜索素材名称"]').setValue('素材13')
    expect(wrapper.findAll('.content-picker-card')).toHaveLength(1)
    await wrapper.get('.content-picker-card').trigger('click')
    expect(wrapper.emitted('update:selectedIds')?.[0]).toEqual([['asset-0', 'asset-13']])
    await wrapper.setProps({ selectedIds: ['asset-0', 'asset-13'] })
    await wrapper.get('input[placeholder="搜索素材名称"]').setValue('')
    wrapper.getComponent(ListPagination).vm.$emit('change', { page: 2, page_size: 12 })
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.content-picker-card.is-selected').text()).toContain('素材13')
    await wrapper.get('select').setValue('video')
    expect(wrapper.props('selectedIds')).toEqual(['asset-0', 'asset-13'])
    expect(wrapper.get('.workspace-note').text()).toContain('已选 2 项')
    await wrapper.get('input[type="file"]').trigger('change')
    expect(wrapper.emitted('import')).toHaveLength(1)
    await wrapper.get('.workspace-heading button').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })
})
