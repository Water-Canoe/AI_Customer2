import { defineComponent, h, KeepAlive } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import MessageInboxPage from './MessageInboxPage'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), warning: vi.fn(), error: vi.fn() }))
vi.mock('../shared/api', () => ({ api: { get: mocks.get, post: mocks.post } }))
vi.mock('element-plus', () => ({ ElMessage: { success: vi.fn(), warning: mocks.warning, error: mocks.error } }))

const account = (id: string) => ({ id, name: id, enabled: true, features: ['message'], default_features: [], feature_status: {} })
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
