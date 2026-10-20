import { describe, it, expect } from 'vitest'
import * as api from '@/api'
import { bumpAuthEpoch, currentAuthEpoch, onSessionChanged } from '@/api/session'

describe('401 是否需要清会话（显式白名单）', () => {
  it('认证类端点的 401 不动登录态', () => {
    expect(api.shouldClearSessionOn401({ url: '/api/auth/login' })).toBe(false)
    expect(api.shouldClearSessionOn401({ url: '/api/auth/register' })).toBe(false)
    expect(api.shouldClearSessionOn401({ url: '/api/auth/verify' })).toBe(false)
    expect(api.shouldClearSessionOn401({ url: '/api/auth/verify-email' })).toBe(false)
    expect(api.shouldClearSessionOn401({ url: '/api/auth/resend-verification' })).toBe(false)
  })

  it('普通业务端点与缺省配置仍然清会话', () => {
    expect(api.shouldClearSessionOn401({ url: '/api/designs/1' })).toBe(true)
    expect(api.shouldClearSessionOn401(undefined)).toBe(true)
  })

  it('带匿名访问令牌的请求不清会话（401 属于该令牌）', () => {
    expect(api.shouldClearSessionOn401({
      url: '/api/sequencing/abc', headers: { 'X-Access-Token': 'tok' },
    })).toBe(false)
    // axios 的 AxiosHeaders 形态
    const headers = { get: (k: string) => (k === 'X-Access-Token' ? 'tok' : undefined) }
    expect(api.shouldClearSessionOn401({ url: '/api/sequencing/abc', headers })).toBe(false)
  })
})

describe('会话代际跨标签页同步', () => {
  it('storage 事件让本页跟随更高的代际并通知回调', () => {
    const seen: number[] = []
    onSessionChanged(() => seen.push(currentAuthEpoch()))
    const before = currentAuthEpoch()

    window.dispatchEvent(new StorageEvent('storage', {
      key: 'pd_auth_epoch', newValue: String(before + 7),
    }))

    expect(currentAuthEpoch()).toBe(before + 7)
    expect(seen).toEqual([before + 7])
    onSessionChanged(null)
  })

  it('更低或相同的代际被忽略（避免回声）', () => {
    const before = currentAuthEpoch()
    let called = 0
    onSessionChanged(() => { called++ })

    window.dispatchEvent(new StorageEvent('storage', {
      key: 'pd_auth_epoch', newValue: String(before),
    }))
    window.dispatchEvent(new StorageEvent('storage', {
      key: 'pd_auth_epoch', newValue: String(Math.max(0, before - 3)),
    }))

    expect(currentAuthEpoch()).toBe(before)
    expect(called).toBe(0)
    onSessionChanged(null)
  })

  it('bump 写入存储，供不支持 BroadcastChannel 的环境兜底', () => {
    const next = bumpAuthEpoch()
    expect(currentAuthEpoch()).toBe(next)
    expect(localStorage.getItem('pd_auth_epoch')).toBe(String(next))
  })
})
