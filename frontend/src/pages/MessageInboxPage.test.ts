import { defineComponent, h, KeepAlive } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MessageInboxPage from './MessageInboxPage'
import { ListPagination } from '../components/ui/ListPagination'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn(), warning: vi.fn(), error: vi.fn() }))
vi.mock('../shared/api', () => ({ api: { get: mocks.get, post: mocks.post, delete: mocks.delete } }))
vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    ElMessage: { success: vi.fn(), warning: mocks.warning, error: mocks.error },
    ElPagination: { render: () => null },
    ElDrawer: defineComponent({ props: { modelValue: Boolean }, setup: (props, { slots }) => () => props.modelValue ? h('div', slots.default?.()) : null }),
  }
})

const account = (id: string, platform = 'dy') => ({ id, platform, name: id, enabled: true, features: ['message'], default_features: [], feature_status: {} })
const conversation = (id: number, account_id = 'account-a') => ({ id, account_id, peer_user_id: 'peer-' + id, nickname: '会话' + id, avatar: '', last_message: '', unread_count: 0, lead_id: null })
const message = (id: number, content: string, status = 'received') => ({ id, direction: status === 'received' ? 'incoming' : 'outgoing', content, message_type: 'text', status, created_at: '2026-10-07 12:00' })
const status = { state: 'disconnected', receive_ready: true, send_ready: true, error: '', history_scope: 'local' }
let wrapper: VueWrapper | undefined

async function open(path = '/message-inbox?account_id=account-a&conversation=1') {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/message-inbox', name: 'message-inbox', component: MessageInboxPage },
    { path: '/other', name: 'other', component: defineComponent({ render: () => h('p', '其它页签') }) },
  ] })
  await router.push(path)
  // 使用真实 KeepAlive 验证页签离开后没有遗留的前端轮询。
  wrapper = mount(defineComponent({ setup: () => () => h(RouterView, {}, { default: ({ Component }: any) => h(KeepAlive, {}, () => h(Component)) }) }), { global: { plugins: [router] } })
  await flushPromises()
  return router
}

describe('私信收件箱', () => {
  it('filters accounts by platform and keeps chat drafts separate when switching', async () => {
    const normal = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation(async (url: string, options: any) => url === '/accounts'
      ? { data: [account('account-a'), account('account-b'), account('xhs-account', 'xhs')] }
      : url === '/message-inbox/conversations' && options.params.account_id === 'xhs-account'
        ? { data: [conversation(2, 'xhs-account')] } : normal(url, options))
    const router = await open()
    await wrapper!.get('textarea').setValue('抖音草稿')
    await wrapper!.get('[aria-label="私信平台"]').setValue('xhs')
    await flushPromises()
    expect(router.currentRoute.value.query.platform).toBe('xhs')
    expect(mocks.get).toHaveBeenCalledWith('/accounts', { params: { feature: 'message', platform: 'xhs' } })
    const picker = wrapper!.get('[aria-label="私信账号"]')
    expect(picker.findAll('option').map(item => item.attributes('value'))).toEqual(['xhs-account'])
    expect(wrapper!.find('textarea').exists()).toBe(false)
    await wrapper!.get('.inbox-conversation').trigger('click')
    await flushPromises()
    expect(wrapper!.get('.inbox-chat-header').text()).toContain('小红书私信')
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    await wrapper!.get('textarea').setValue('小红书草稿')
    await router.replace('/message-inbox?platform=dy&account_id=account-a&conversation=1')
    await flushPromises()
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('抖音草稿')
    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('discards an old platform response after a switch and does not reuse its account', async () => {
    const router = await open()
    const normal = mocks.get.getMockImplementation()!
    let finishOld!: (value: any) => void
    mocks.get.mockImplementation((url: string, options: any) => {
      if (url.endsWith('/status') && url.includes('account-a')) return new Promise(resolve => { finishOld = resolve })
      if (url === '/accounts' && options.params.platform === 'xhs') return Promise.resolve({ data: [account('xhs-account', 'xhs')] })
      return normal(url, options)
    })
    await vi.advanceTimersByTimeAsync(3000)
    await wrapper!.get('[aria-label="私信平台"]').setValue('xhs')
    await flushPromises()
    expect(wrapper!.find('textarea').exists()).toBe(false)
    finishOld({ data: { ...status, state: 'connected' } })
    await flushPromises()
    expect(wrapper!.text()).not.toContain('接收已连接')
    expect(router.currentRoute.value.query.platform).toBe('xhs')
    expect((wrapper!.get('[aria-label="私信账号"]').element as HTMLSelectElement).value).toBe('xhs-account')
    expect(mocks.post).not.toHaveBeenCalled()
  })

  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    mocks.get.mockImplementation(async (url: string, options: any) => {
      if (url === '/accounts') return { data: [account('account-a'), account('account-b')] }
      if (url.endsWith('/status')) return { data: status }
      if (url === '/message-inbox/conversations') return { data: options.params.account_id === 'account-b' ? [conversation(2, 'account-b')] : [conversation(1), conversation(3)] }
      if (url.endsWith('/messages')) return { data: { messages: [message(10, '当前消息')], has_more: false } }
      return { data: {} }
    })
    mocks.post.mockResolvedValue({ data: status })
    mocks.delete.mockResolvedValue({ data: { ok: true } })
  })
  afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.useRealTimers() })

  it('loads authorized accounts and local records without connecting or sending automatically', async () => {
    await open()
    expect(wrapper!.text()).toContain('尚未同步接入前的全部历史')
    expect(wrapper!.text()).toContain('当前消息')
    expect(mocks.get).toHaveBeenCalledWith('/accounts', { params: { feature: 'message', platform: 'dy' } })
    expect(mocks.post).not.toHaveBeenCalled()
    await wrapper!.findAll('button').find(item => item.text() === '连接接收')!.trigger('click')
    await flushPromises()
    expect(mocks.post).toHaveBeenCalledWith('/message-inbox/accounts/account-a/connect')
    await wrapper!.findAll('button').find(item => item.text() === '同步登录认证')!.trigger('click')
    await flushPromises()
    expect(mocks.post).toHaveBeenCalledWith('/message-inbox/accounts/account-a/sync-auth')
  })

  it.each([
    ['syncing', '正在同步认证'],
    ['logging_in', '登录中，私信已暂停'],
  ])('shows %s and blocks conflicting actions until it finishes', async (state, label) => {
    const normal = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation(async (url: string, options: any) => url.endsWith('/status')
      ? { data: { ...status, state, receive_ready: false, send_ready: false } }
      : normal(url, options))
    await open()
    expect(wrapper!.text()).toContain(label)
    for (const label of ['同步登录认证', '连接接收', '发送']) {
      expect(wrapper!.findAll('button').find(button => button.text() === label)!.attributes()).toHaveProperty('disabled')
    }
    mocks.get.mockImplementation(async (url: string, options: any) => url.endsWith('/status')
      ? { data: { ...status, state: 'connected' } } : normal(url, options))
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(wrapper!.text()).toContain('接收已连接')
    expect(wrapper!.findAll('button').find(button => button.text() === '断开接收')!.attributes()).not.toHaveProperty('disabled')
  })

  it('preserves separate chat drafts and displays uncertain submission without automatically retrying', async () => {
    const router = await open()
    await wrapper!.get('textarea').setValue('第一段未发送内容')
    await router.replace('/message-inbox?account_id=account-a&conversation=3')
    await flushPromises()
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    await wrapper!.get('textarea').setValue('另一段草稿')
    await router.replace('/message-inbox?account_id=account-a&conversation=1')
    await flushPromises()
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('第一段未发送内容')
    mocks.post.mockResolvedValueOnce({ data: message(20, '第一段未发送内容', 'uncertain') })
    await wrapper!.get('.inbox-composer').trigger('submit')
    await flushPromises()
    expect(mocks.post).toHaveBeenCalledWith('/message-inbox/conversations/1/messages', { account_id: 'account-a', text: '第一段未发送内容' })
    expect(wrapper!.text()).toContain('结果待确认')
    expect(mocks.warning).toHaveBeenCalled()
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    await vi.advanceTimersByTimeAsync(6000)
    expect(mocks.post.mock.calls.filter(([url]) => url.endsWith('/messages'))).toHaveLength(1)
  })
  it.each([false, true])('releases the send button while refresh is pending after a send (failure: %s)', async failure => {
    await open()
    let finishSend!: (value: any) => void
    let rejectSend!: (cause: any) => void
    let finishRefresh!: (value: any) => void
    mocks.post.mockImplementationOnce(() => new Promise((resolve, reject) => { finishSend = resolve; rejectSend = reject }))
    const normal = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options: any) => url.endsWith('/status')
      ? new Promise(resolve => { finishRefresh = resolve }) : normal(url, options))
    await wrapper!.get('textarea').setValue('本次发送内容')
    await wrapper!.get('.inbox-composer').trigger('submit')
    expect(wrapper!.get('.inbox-composer button').text()).toBe('发送中…')
    // 等待期间编辑下一条；发送结束后即使刷新未返回，也应能继续操作。
    await wrapper!.get('textarea').setValue('下一条草稿')
    if (failure) rejectSend({ response: { data: { detail: '平台拒绝发送' } } })
    else finishSend({ data: message(20, '本次发送内容', 'accepted') })
    await flushPromises()
    expect(finishRefresh).toBeTypeOf('function')
    expect(wrapper!.get('.inbox-composer button').text()).toBe('发送')
    expect((wrapper!.get('.inbox-composer button').element as HTMLButtonElement).disabled).toBe(false)
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('下一条草稿')
    if (failure) expect(mocks.error).toHaveBeenCalledWith('平台拒绝发送')
    else expect(wrapper!.text()).toContain('本次发送内容')
    finishRefresh({ data: status })
    await flushPromises()
  })
  it('shows the same real nickname and avatar in the conversation list and chat header', async () => {
    const normal = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options: any) => url === '/message-inbox/conversations'
      ? Promise.resolve({ data: [{ ...conversation(1), nickname: '真实用户名', avatar: 'https://example.com/avatar.jpg' }, conversation(3)] })
      : normal(url, options))
    await open()
    expect(wrapper!.get('.inbox-conversation-title strong').text()).toBe('真实用户名')
    expect(wrapper!.get('.inbox-chat-header strong').text()).toBe('真实用户名')
    expect(wrapper!.get('.inbox-conversation img').attributes('src')).toBe('https://example.com/avatar.jpg')
    expect(wrapper!.get('.inbox-chat-header img').attributes('src')).toBe('https://example.com/avatar.jpg')
    expect(wrapper!.findAll('img')).toHaveLength(2)
    // 图片失效时两处统一退回占位，保留平台返回的用户名。
    await wrapper!.get('.inbox-conversation img').trigger('error')
    expect(wrapper!.findAll('img')).toHaveLength(0)
    expect(wrapper!.get('.inbox-chat-header').text()).toContain('真实用户名')
  })
  it('shows a profile lookup error alongside the account error without hiding local messages', async () => {
    const normal = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options: any) => url.endsWith('/status')
      ? Promise.resolve({ data: { ...status, error: '账号认证异常', profile_error: '用户资料获取失败，请重新连接后重试' } })
      : normal(url, options))
    await open()
    expect(wrapper!.findAll('[role="alert"]').map(item => item.text())).toEqual(['账号认证异常', '用户资料获取失败，请重新连接后重试'])
    expect(wrapper!.text()).toContain('当前消息')
  })
  it.each([
    ['reconnecting', '正在自动重连', true],
    ['expired', '接收认证已失效', false],
    ['error', '接收已停止', false],
  ])('shows %s without silently starting another connection', async (state, label, reconnecting) => {
    const normal = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options: any) => url.endsWith('/status')
      ? Promise.resolve({ data: { ...status, state, error: '连接状态提示' } }) : normal(url, options))
    await open()
    expect(wrapper!.get('[role="status"]').text()).toBe(label)
    expect(wrapper!.findAll('button').some(item => item.text() === '断开接收')).toBe(reconnecting)
    await vi.advanceTimersByTimeAsync(4000)
    expect(mocks.post).not.toHaveBeenCalled()
    expect(wrapper!.text()).toContain('当前消息')
  })
  it('imports a prepared draft once and keeps subsequent edits through refresh and tab restoration', async () => {
    const router = await open('/message-inbox?account_id=account-a&conversation=1&draft=初始话术')
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('初始话术')
    expect(router.currentRoute.value.query).not.toHaveProperty('draft')
    await wrapper!.get('textarea').setValue('用户修改后的话术')
    await vi.advanceTimersByTimeAsync(4000)
    await router.push('/other')
    await router.push('/message-inbox?account_id=account-a&conversation=1')
    await flushPromises()
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('用户修改后的话术')
    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('opens linked customer detail locally and keeps evidence and history pages without changing the chat or draft', async () => {
    const normal = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options: any) => {
      if (url === '/message-inbox/conversations') return Promise.resolve({ data: [{ ...conversation(1), lead_id: 7 }] })
      if (url === '/message-workbench/customers/7') return Promise.resolve({ data: {
        customer: { lead_id: 7, nickname: '关联客户七', platform: 'dy', script: '客户专属话术', reason: '明确表达采购需求' },
        sources: Array.from({ length: 11 }, (_, i) => ({ keyword: '采购', comment_text: `来源证据${i + 1}` })),
        events: Array.from({ length: 11 }, (_, i) => ({ from_status: '未私信', to_status: '已回复', note: `跟进记录${i + 1}` })),
      } })
      return normal(url, options)
    })
    const router = await open()
    const path = router.currentRoute.value.fullPath
    await wrapper!.get('textarea').setValue('保留当前聊天草稿')
    const requests = mocks.get.mock.calls.length
    await wrapper!.findAll('button').find(item => item.text() === '查看客户详情')!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe(path)
    expect(wrapper!.get('.inbox-chat-header').text()).toContain('会话1')
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('保留当前聊天草稿')
    expect(mocks.get.mock.calls).toHaveLength(requests)
    expect(mocks.post).not.toHaveBeenCalled()
    let drawer = wrapper!.get('.message-detail-drawer')
    expect(drawer.text()).toContain('客户专属话术')
    expect(drawer.text()).toContain('明确表达采购需求')
    expect(drawer.text()).toContain('来源证据1')
    expect(drawer.findAll('button').some(item => item.text() === '打开私信会话')).toBe(false)
    drawer.findComponent(ListPagination).vm.$emit('change', { page: 2, page_size: 10 })
    await flushPromises()
    expect(drawer.findAll('.source-card')).toHaveLength(1)
    expect(drawer.text()).toContain('来源证据11')
    await drawer.findAll('button').find(item => item.text().startsWith('跟进历史'))!.trigger('click')
    drawer.findComponent(ListPagination).vm.$emit('change', { page: 2, page_size: 10 })
    await flushPromises()
    expect(drawer.get('.event-timeline').text()).toContain('跟进记录11')
    await drawer.findAll('button').find(item => item.text().startsWith('来源证据'))!.trigger('click')
    expect(drawer.findAll('.source-card')).toHaveLength(1)
    expect(drawer.text()).toContain('来源证据11')
    await drawer.get('[aria-label="关闭详情"]').trigger('click')
    expect(wrapper!.find('.message-detail-drawer').exists()).toBe(false)
    expect(router.currentRoute.value.fullPath).toBe(path)
    await wrapper!.findAll('button').find(item => item.text() === '查看客户详情')!.trigger('click')
    drawer = wrapper!.get('.message-detail-drawer')
    expect(drawer.text()).toContain('来源证据11')
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('保留当前聊天草稿')
    // 缓存页的抽屉只属于收件箱；换会话后必须关闭并重置其分页。
    await router.push('/other')
    await flushPromises()
    expect(wrapper!.find('.message-detail-drawer').exists()).toBe(false)
    await router.push(path)
    await flushPromises()
    expect(wrapper!.get('.message-detail-drawer').text()).toContain('来源证据11')
    await router.replace('/message-inbox?account_id=account-a&conversation=3')
    await flushPromises()
    expect(wrapper!.find('.message-detail-drawer').exists()).toBe(false)
    await router.replace(path)
    await flushPromises()
    expect(wrapper!.find('.message-detail-drawer').exists()).toBe(false)
    await wrapper!.findAll('button').find(item => item.text() === '查看客户详情')!.trigger('click')
    expect(wrapper!.get('.message-detail-drawer').findAll('.source-card')).toHaveLength(10)
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('保留当前聊天草稿')
  })

  it('refreshes sent and replied states for the same customer without closing detail or losing pages and drafts', async () => {
    let followStatus = '未私信'
    const normal = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options: any) => {
      if (url === '/message-inbox/conversations') return Promise.resolve({ data: [{ ...conversation(1), lead_id: 7 }] })
      if (url === '/message-workbench/customers/7') return Promise.resolve({ data: {
        customer: { lead_id: 7, nickname: '关联客户七', platform: 'dy', follow_status: followStatus },
        sources: Array.from({ length: 11 }, (_, i) => ({ comment_text: `来源证据${i + 1}` })),
        events: followStatus === '已回复' ? [{ from_status: '已私信', to_status: '已回复', note: '收到客户回复' }] : [],
      } })
      return normal(url, options)
    })
    await open()
    await wrapper!.findAll('button').find(item => item.text() === '查看客户详情')!.trigger('click')
    wrapper!.get('.message-detail-drawer').findComponent(ListPagination).vm.$emit('change', { page: 2, page_size: 10 })
    await wrapper!.get('textarea').setValue('首次发送内容')
    followStatus = '已私信'
    mocks.post.mockResolvedValueOnce({ data: message(20, '首次发送内容', 'accepted') })
    await wrapper!.get('.inbox-composer').trigger('submit')
    await flushPromises()
    expect(wrapper!.get('.inbox-customer').text()).toContain('跟进：已私信')
    expect(wrapper!.get('.message-detail-drawer').text()).toContain('来源证据11')
    await wrapper!.get('textarea').setValue('保留草稿')
    followStatus = '已回复'
    await vi.advanceTimersByTimeAsync(4000)
    expect(wrapper!.get('.inbox-customer').text()).toContain('跟进：已回复')
    expect(wrapper!.get('.message-detail-drawer').text()).toContain('跟进历史 (1)')
    expect(wrapper!.get('.message-detail-drawer').findAll('.source-card')).toHaveLength(1)
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('保留草稿')
  })

  it('removes the selected conversation from the list and restores its draft when it becomes visible again', async () => {
    let hidden = false
    const normal = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options: any) => url === '/message-inbox/conversations'
      ? Promise.resolve({ data: hidden ? [conversation(3)] : [conversation(1), conversation(3)] }) : normal(url, options))
    mocks.delete.mockImplementation(async () => { hidden = true; return { data: { ok: true } } })
    const router = await open()
    await wrapper!.get('textarea').setValue('删除会话仍保留草稿')
    expect(wrapper!.find('.inbox-conversation button').exists()).toBe(false)
    await wrapper!.get('[aria-label="删除会话：会话1"]').trigger('click')
    await flushPromises()
    expect(mocks.delete).toHaveBeenCalledWith('/message-inbox/conversations/1', { params: { account_id: 'account-a' } })
    expect(router.currentRoute.value.query).not.toHaveProperty('conversation')
    expect(wrapper!.find('textarea').exists()).toBe(false)
    expect(wrapper!.findAll('.inbox-conversation-title strong').map(item => item.text())).toEqual(['会话3'])
    await vi.advanceTimersByTimeAsync(4000)
    expect(wrapper!.findAll('.inbox-conversation')).toHaveLength(1)
    // 新消息使原会话重新可见，选择后仍能查看原记录、继续编辑原草稿。
    hidden = false
    await vi.advanceTimersByTimeAsync(4000)
    await wrapper!.findAll('.inbox-conversation').find(item => item.text().includes('会话1'))!.trigger('click')
    await flushPromises()
    expect(wrapper!.text()).toContain('当前消息')
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('删除会话仍保留草稿')
  })

  it('clears the linked customer and drawer when search removes the selected conversation', async () => {
    const normal = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options: any) => {
      if (url === '/message-inbox/conversations') return Promise.resolve({ data: options.params.query ? [] : [{ ...conversation(1), lead_id: 7 }] })
      if (url === '/message-workbench/customers/7') return Promise.resolve({ data: {
        customer: { lead_id: 7, nickname: '当前关联客户', platform: 'dy' }, sources: [], events: [],
      } })
      return normal(url, options)
    })
    await open()
    await wrapper!.get('textarea').setValue('保留聊天草稿')
    await wrapper!.findAll('button').find(item => item.text() === '查看客户详情')!.trigger('click')
    expect(wrapper!.find('.message-detail-drawer').exists()).toBe(true)
    await wrapper!.get('[aria-label="搜索会话"]').setValue('不匹配的会话')
    await wrapper!.get('.inbox-search').trigger('submit')
    await flushPromises()
    expect(wrapper!.get('.inbox-customer').text()).toContain('选择会话后查看客户信息')
    expect(wrapper!.find('.message-detail-drawer').exists()).toBe(false)
    await wrapper!.get('[aria-label="搜索会话"]').setValue('')
    await wrapper!.get('.inbox-search').trigger('submit')
    await flushPromises()
    expect(wrapper!.get('.inbox-customer').text()).toContain('当前关联客户')
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('保留聊天草稿')
  })

  it('keeps the current chat when another conversation is removed and rejects a stale list response', async () => {
    let finish!: (value: any) => void
    const router = await open()
    await wrapper!.get('textarea').setValue('当前会话草稿')
    const normal = mocks.get.getMockImplementation()!
    let oldPoll = true
    mocks.get.mockImplementation((url: string, options: any) => {
      if (url === '/message-inbox/conversations') {
        if (oldPoll) { oldPoll = false; return new Promise(resolve => { finish = resolve }) }
        return Promise.resolve({ data: [conversation(1)] })
      }
      return normal(url, options)
    })
    await vi.advanceTimersByTimeAsync(4000)
    await wrapper!.get('[aria-label="删除会话：会话3"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.conversation).toBe('1')
    expect(wrapper!.get('.inbox-chat-header').text()).toContain('会话1')
    finish({ data: [conversation(1), conversation(3)] })
    await flushPromises()
    expect(wrapper!.findAll('.inbox-conversation-title strong').map(item => item.text())).toEqual(['会话1'])
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('当前会话草稿')
  })

  it('keeps the conversation and draft after a failed deletion', async () => {
    const router = await open()
    await wrapper!.get('textarea').setValue('未发送内容')
    mocks.delete.mockRejectedValueOnce({ response: { data: { detail: '删除会话被拒绝' } } })
    await wrapper!.get('[aria-label="删除会话：会话1"]').trigger('click')
    await flushPromises()
    expect(mocks.error).toHaveBeenCalledWith('删除会话被拒绝')
    expect(wrapper!.findAll('.inbox-conversation')).toHaveLength(2)
    expect(router.currentRoute.value.query.conversation).toBe('1')
    expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe('未发送内容')
    expect((wrapper!.get('[aria-label="删除会话：会话1"]').element as HTMLButtonElement).disabled).toBe(false)
  })

  it.each([
    ['/message-inbox?account_id=account-b&conversation=2', '关联客户八'],
    ['/message-inbox?account_id=account-a&conversation=3', '关联客户九'],
    ['/other', ''],
  ])('ignores a late linked customer response after switching to %s', async (path, currentCustomer) => {
    let finish!: (value: any) => void
    const normal = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options: any) => {
      if (url === '/message-inbox/conversations') return Promise.resolve({ data: options.params.account_id === 'account-b'
        ? [{ ...conversation(2, 'account-b'), lead_id: 8 }]
        : [{ ...conversation(1), lead_id: 7 }, { ...conversation(3), lead_id: 9 }] })
      if (url === '/message-workbench/customers/7') return new Promise(resolve => { finish = resolve })
      if (url === '/message-workbench/customers/8' || url === '/message-workbench/customers/9') return Promise.resolve({ data: {
        customer: { lead_id: Number(url.split('/').pop()), nickname: currentCustomer, platform: 'dy' }, sources: [], events: [],
      } })
      return normal(url, options)
    })
    const router = await open()
    expect(finish).toBeTypeOf('function')
    await router.replace(path)
    finish({ data: { customer: { lead_id: 7, nickname: '迟到的旧客户', platform: 'dy' }, sources: [{ comment_text: '旧客户证据' }], events: [] } })
    await flushPromises()
    expect(wrapper!.text()).not.toContain('迟到的旧客户')
    expect(wrapper!.find('.message-detail-drawer').exists()).toBe(false)
    expect(router.currentRoute.value.fullPath).toBe(path)
    if (currentCustomer) {
      expect(wrapper!.get('.inbox-customer').text()).toContain(currentCustomer)
      await wrapper!.findAll('button').find(item => item.text() === '查看客户详情')!.trigger('click')
      expect(wrapper!.get('.message-detail-drawer').text()).toContain(currentCustomer)
      expect(wrapper!.get('.message-detail-drawer').text()).not.toContain('旧客户证据')
    }
    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('ignores an old account response after switching and never renders its messages in the new account', async () => {
    let finish!: (value: any) => void
    const normal = mocks.get.getMockImplementation()!
    mocks.get.mockImplementation((url: string, options: any) => url === '/message-inbox/conversations/1/messages'
      ? new Promise(resolve => { finish = resolve }) : normal(url, options))
    const router = await open()
    await router.replace('/message-inbox?account_id=account-b&conversation=2')
    finish({ data: { messages: [message(99, '旧账号迟到的消息')], has_more: false } })
    await flushPromises()
    expect(wrapper!.text()).not.toContain('旧账号迟到的消息')
    expect(wrapper!.get('.inbox-chat-header').text()).toContain('会话2')
    expect((wrapper!.get('[aria-label="私信账号"]').element as HTMLSelectElement).value).toBe('account-b')
    expect(mocks.get).toHaveBeenCalledWith('/message-inbox/conversations/2/messages', { params: { account_id: 'account-b', limit: 50 } })
  })

  it('keeps earlier records across refresh and pauses polling while hidden, deactivated or unmounted', async () => {
    mocks.get.mockImplementation(async (url: string, options: any) => {
      if (url === '/accounts') return { data: [account('account-a')] }
      if (url.endsWith('/status')) return { data: status }
      if (url === '/message-inbox/conversations') return { data: [conversation(1)] }
      return { data: { messages: options?.params.before_id ? [message(1, '更早记录')] : [message(10, '较新记录')], has_more: !options?.params.before_id } }
    })
    const router = await open()
    await wrapper!.findAll('button').find(item => item.text() === '加载更早记录')!.trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(4000)
    expect(wrapper!.findAll('.inbox-message-content').map(item => item.text())).toEqual(['更早记录', '较新记录'])
    mocks.get.mockClear()
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    await vi.advanceTimersByTimeAsync(6000)
    expect(mocks.get).not.toHaveBeenCalled()
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    await router.push('/other')
    await flushPromises()
    mocks.get.mockClear()
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(6000)
    expect(mocks.get).not.toHaveBeenCalled()
    await router.push('/message-inbox?account_id=account-a&conversation=1')
    await flushPromises()
    expect(mocks.get).toHaveBeenCalled()
    wrapper!.unmount(); wrapper = undefined
    mocks.get.mockClear()
    await vi.advanceTimersByTimeAsync(6000)
    expect(mocks.get).not.toHaveBeenCalled()
  })
})
