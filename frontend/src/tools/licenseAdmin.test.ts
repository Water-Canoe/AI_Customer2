import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const html = readFileSync(resolve(process.cwd(), '../tools/license-admin.html'), 'utf8')

describe('本地授权管理员页面的设备操作契约', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    document.body.innerHTML = new DOMParser().parseFromString(html, 'text/html').body.innerHTML
    vi.stubGlobal('confirm', () => true)
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  it.each(['revoke', 'restore'] as const)('%s 发送 JSON 对象，并刷新授权与设备列表', async action => {
    // 执行真实页面脚本；传输替身遵循新后端“写请求必须为 JSON 对象”的约束。
    const script = html.match(/<script>([\s\S]*?)<\/script>/)![1]
    const page = new Function(script + '\nreturn {state,revokeDevice,restoreDevice};')()
    const license = {
      licenseId: '0123456789abcdef01234567', licenseCode: 'AIC-TEST', codePrefix: 'AIC-TEST',
      status: 'active', entitlements: ['lead'], maxDevices: 1, activeDeviceCount: 1,
      expiresAt: null, remark: '',
    }
    const deviceId = 'AI-CUS-TEST-DEVICE'
    page.state.licenses = [license]
    page.state.selectedId = license.licenseId
    ;(document.getElementById('adminToken') as HTMLInputElement).value = 'test-admin-token'

    const fetch = vi.fn(async (url: string, options: RequestInit) => {
      const headers = options.headers as Record<string, string>
      const valid = options.method === 'GET' ||
        (headers['Content-Type'] === 'application/json' && options.body === '{}')
      const data = url.endsWith('/devices') ? [{ deviceId, status: action === 'revoke' ? 'revoked' : 'active' }] :
        options.method === 'GET' ? [license] : {}
      return {
        ok: valid, status: valid ? 200 : 400,
        json: async () => ({ code: valid ? 200 : 400, message: '请求体必须为 JSON 对象', data }),
      }
    })
    vi.stubGlobal('fetch', fetch)
    await page[action === 'revoke' ? 'revokeDevice' : 'restoreDevice'](deviceId)

    const base = 'https://puxaxxvlzefd.sealosbja.site/api/v1/products/ai-customer'
    expect(fetch.mock.calls[0][0]).toBe(`${base}/admin/licenses/${license.licenseId}/devices/${deviceId}/${action}`)
    expect(fetch.mock.calls[0][1]).toMatchObject({
      method: 'POST', body: '{}',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer test-admin-token' },
    })
    expect(fetch.mock.calls.slice(1).map(([url]) => url)).toEqual([
      `${base}/admin/licenses?search=`, `${base}/admin/licenses/${license.licenseId}/devices`,
    ])
    expect(document.getElementById('deviceTotal')?.textContent).toBe('共 1 台')
  })
})
