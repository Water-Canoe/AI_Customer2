import { describe, expect, it } from 'vitest'

import { paginateItems } from './ListPagination'


describe('paginateItems', () => {
  it('分页并把越界页码收敛到最后一页', () => {
    const result = paginateItems([1, 2, 3, 4, 5], 9, 2)
    expect(result).toMatchObject({ items: [5], page: 3, pageSize: 2, total: 5, totalPages: 3, start: 4 })
  })

  it('空列表仍保留第一页', () => {
    expect(paginateItems([], 2, 20)).toMatchObject({ items: [], page: 1, total: 0, totalPages: 1 })
  })
})
