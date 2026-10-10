import { describe, it, expect, vi, beforeEach } from 'vitest'

// 捕获 api 模块注册的拦截器，直接驱动它们
const handlers = vi.hoisted(() => ({
  request: null as null | ((c: any) => any),
  responseError: null as null | ((e: any) => Promise<any>)
}))
const clearAuth = vi.hoisted(() => vi.fn())

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
      interceptors: {
        request: { use: vi.fn((ok: any) => { handlers.request = ok }) },
        response: { use: vi.fn((_ok: any, err: any) => { handlers.responseError = err }) }
      }
    }))
  }
}))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ clearAuth })
}))

import '@/api'

function sendWith(token: string | null) {
  if (token) localStorage.setItem('token', token)
  else localStorage.removeItem('token')
  return handlers.request!({ url: '/vectors', headers: {} })
}

function fail401(config: any) {
  return handlers.responseError!({ config, response: { status: 401 } }).catch(() => undefined)
}

describe('401 只清理发出请求时的会话', () => {
  beforeEach(() => {
    localStorage.clear()
    clearAuth.mockReset()
  })

  it('当前会话的 401 清理登录态', async () => {
    const cfg = sendWith('tok-A')
    localStorage.setItem('user', '{"id":1}')
    await fail401(cfg)
    expect(localStorage.getItem('token')).toBeNull()
    expect(localStorage.getItem('user')).toBeNull()
    expect(clearAuth).toHaveBeenCalledTimes(1)
  })

  it('旧 token 请求迟到的 401 不清理重新登录后的新会话', async () => {
    const stale = sendWith('tok-A')
    // 请求在途期间用户重新登录
    localStorage.setItem('token', 'tok-B')
    localStorage.setItem('user', '{"id":2}')
    await fail401(stale)
    expect(localStorage.getItem('token')).toBe('tok-B')
    expect(localStorage.getItem('user')).toBe('{"id":2}')
    expect(clearAuth).not.toHaveBeenCalled()
  })

  it('未登录时发出的请求在登录后才返回 401，不清理新会话', async () => {
    const anon = sendWith(null)
    localStorage.setItem('token', 'tok-B')
    await fail401(anon)
    expect(localStorage.getItem('token')).toBe('tok-B')
    expect(clearAuth).not.toHaveBeenCalled()
  })

  it('登录接口自身的 401 不清理会话', async () => {
    localStorage.setItem('token', 'tok-A')
    await fail401({ url: '/auth/login', headers: {} })
    expect(localStorage.getItem('token')).toBe('tok-A')
  })

  it('错误照常向上抛出', async () => {
    const cfg = sendWith('tok-A')
    const err = { config: cfg, response: { status: 401 } }
    await expect(handlers.responseError!(err)).rejects.toBe(err)
  })
})
