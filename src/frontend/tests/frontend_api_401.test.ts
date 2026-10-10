import { describe, it, expect, vi, beforeEach } from 'vitest'

// 捕获 api 模块注册的拦截器，直接驱动它们
const handlers = vi.hoisted(() => ({
  request: null as null | ((c: any) => any),
  responseError: null as null | ((e: any) => Promise<any>),
  createConfig: null as any
}))
const clearAuth = vi.hoisted(() => vi.fn())

vi.mock('axios', () => ({
  default: {
    create: vi.fn((config: any) => {
      handlers.createConfig = config
      return {
        get: vi.fn(),
        post: vi.fn(),
        put: vi.fn(),
        delete: vi.fn(),
        interceptors: {
          request: { use: vi.fn((ok: any) => { handlers.request = ok }) },
          response: { use: vi.fn((_ok: any, err: any) => { handlers.responseError = err }) }
        }
      }
    })
  }
}))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ clearAuth })
}))

import '@/api'
import { bumpAuthEpoch, currentAuthEpoch } from '@/api/session'

function send(url = '/vectors') {
  return handlers.request!({ url, headers: {} })
}

function fail401(config: any) {
  return handlers.responseError!({ config, response: { status: 401 } }).catch(() => undefined)
}

describe('axios 实例走 Cookie 会话', () => {
  it('携带凭据并附 CSRF 自定义头，不再读写 localStorage 令牌', () => {
    expect(handlers.createConfig.withCredentials).toBe(true)
    expect(handlers.createConfig.headers['X-Requested-With']).toBe('XMLHttpRequest')
    localStorage.setItem('token', 'legacy')
    const cfg = send()
    expect(cfg.headers.Authorization).toBeUndefined()
  })
})

describe('401 只清理发出请求时的会话', () => {
  beforeEach(() => {
    localStorage.clear()
    clearAuth.mockReset()
  })

  it('当前会话的 401 清理登录态', async () => {
    const cfg = send()
    await fail401(cfg)
    expect(clearAuth).toHaveBeenCalledTimes(1)
  })

  it('旧会话请求迟到的 401 不清理重新登录后的新会话', async () => {
    const stale = send()
    // 请求在途期间用户重新登录（会话代际递增）
    bumpAuthEpoch()
    await fail401(stale)
    expect(clearAuth).not.toHaveBeenCalled()
  })

  it('未登录时发出的请求在登录后才返回 401，不清理新会话', async () => {
    const anon = send()
    bumpAuthEpoch() // 登录
    await fail401(anon)
    expect(clearAuth).not.toHaveBeenCalled()
  })

  it('请求发出时记录当时的会话代际', () => {
    const before = currentAuthEpoch()
    expect(send().authEpoch).toBe(before)
    bumpAuthEpoch()
    expect(send().authEpoch).toBe(before + 1)
  })

  it('登录接口自身的 401 不清理会话', async () => {
    await fail401({ ...send('/auth/login'), url: '/auth/login' })
    expect(clearAuth).not.toHaveBeenCalled()
  })

  it('错误照常向上抛出', async () => {
    const cfg = send()
    const err = { config: cfg, response: { status: 401 } }
    await expect(handlers.responseError!(err)).rejects.toBe(err)
  })
})
