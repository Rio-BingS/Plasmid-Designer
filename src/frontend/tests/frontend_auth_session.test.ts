/** 会话状态来自后端（httpOnly Cookie）：auth store 不再保存令牌 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'
import { useAuthStore } from '@/stores/auth'
import { bumpAuthEpoch, currentAuthEpoch, notifySessionExpired } from '@/api/session'
import * as api from '@/api'

vi.mock('@/api', () => ({
  login: vi.fn(),
  register: vi.fn(),
  logout: vi.fn(),
  verifyToken: vi.fn(),
  getSiteConfig: vi.fn(() => Promise.resolve(null))
}))

const alice = { id: 'u1', email: 'a@test.com', username: 'alice', is_admin: false }

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

describe('auth store 会话', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('登录只记录用户信息，不把令牌写进 localStorage', async () => {
    vi.mocked(api.login).mockResolvedValue({ access_token: 'secret', user: alice })
    const store = useAuthStore()
    await store.login('a@test.com', 'pw')
    expect(store.isAuthenticated).toBe(true)
    expect(store.user).toEqual(alice)
    expect(localStorage.length).toBe(0)
    expect('token' in store).toBe(false)
  })

  it('登录建立新会话代际', async () => {
    vi.mocked(api.login).mockResolvedValue({ user: alice })
    const before = currentAuthEpoch()
    await useAuthStore().login('a@test.com', 'pw')
    expect(currentAuthEpoch()).toBeGreaterThan(before)
  })

  it('启动时清除旧版本遗留的本地令牌，并按 /auth/verify 恢复会话', async () => {
    localStorage.setItem('token', 'legacy-token')
    localStorage.setItem('user', JSON.stringify(alice))
    vi.mocked(api.verifyToken).mockResolvedValue({ valid: true, user: alice })
    const store = useAuthStore()
    store.initSession()
    expect(localStorage.getItem('token')).toBeNull()
    expect(localStorage.getItem('user')).toBeNull()
    await flushPromises()
    expect(store.user).toEqual(alice)
    expect(api.getSiteConfig).toHaveBeenCalled()
  })

  it('Cookie 无效时 /auth/verify 返回 valid=false，保持未登录', async () => {
    vi.mocked(api.verifyToken).mockResolvedValue({ valid: false, user: null })
    const store = useAuthStore()
    expect(await store.ensureSession()).toBe(false)
    expect(store.isAuthenticated).toBe(false)
  })

  it('会话只核实一次（路由守卫与导航栏共享结果）', async () => {
    vi.mocked(api.verifyToken).mockResolvedValue({ valid: true, user: alice })
    const store = useAuthStore()
    await Promise.all([store.ensureSession(), store.ensureSession()])
    store.initSession()
    await flushPromises()
    expect(api.verifyToken).toHaveBeenCalledTimes(1)
  })

  it('核实期间用户已登录：迟到的 valid=false 不清掉新会话', async () => {
    const pending = deferred<any>()
    vi.mocked(api.verifyToken).mockReturnValue(pending.promise)
    vi.mocked(api.login).mockResolvedValue({ user: alice })
    const store = useAuthStore()
    const checking = store.checkAuth()
    await store.login('a@test.com', 'pw')
    pending.resolve({ valid: false, user: null })
    await checking
    expect(store.user).toEqual(alice)
  })

  it('网络错误不清理登录态', async () => {
    vi.mocked(api.verifyToken).mockRejectedValue(new Error('Network Error'))
    const store = useAuthStore()
    store.user = alice
    expect(await store.checkAuth()).toBe(true)
    expect(store.user).toEqual(alice)
  })

  it('登出成功：服务端吊销后清理本地状态', async () => {
    vi.mocked(api.logout).mockResolvedValue({ message: 'ok' })
    const store = useAuthStore()
    store.user = alice
    const before = currentAuthEpoch()
    await store.logout()
    expect(api.logout).toHaveBeenCalledTimes(1)
    expect(store.isAuthenticated).toBe(false)
    expect(currentAuthEpoch()).toBeGreaterThan(before)
  })

  it('登出接口失败（如 429）：保持登录态并抛错，不假装已登出', async () => {
    vi.mocked(api.logout).mockRejectedValue({ response: { status: 429 } })
    const store = useAuthStore()
    store.user = alice
    const before = currentAuthEpoch()
    await expect(store.logout()).rejects.toBeTruthy()
    expect(store.user).toEqual(alice)
    expect(currentAuthEpoch()).toBe(before)
  })

  it('clearAuth 递增会话代际，使旧会话在途请求的 401 失效', () => {
    const store = useAuthStore()
    const before = currentAuthEpoch()
    store.clearAuth()
    expect(currentAuthEpoch()).toBe(before + 1)
    bumpAuthEpoch()
  })

  it('接口 401 经 session 回调清理当前 store 的登录态', () => {
    const store = useAuthStore()
    store.user = alice
    notifySessionExpired()
    expect(store.isAuthenticated).toBe(false)
  })
})

describe('启动会话核实中的 checking 状态', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
  })

  it('初始为 true，首次 /auth/verify 有结果（未登录）后变为 false', async () => {
    const pending = deferred<any>()
    vi.mocked(api.verifyToken).mockReturnValue(pending.promise)
    const store = useAuthStore()
    expect(store.checking).toBe(true)
    store.initSession()
    await flushPromises()
    expect(store.checking).toBe(true)
    pending.resolve({ valid: false, user: null })
    await flushPromises()
    expect(store.checking).toBe(false)
    expect(store.isAuthenticated).toBe(false)
  })

  it('核实为已登录时同样结束 checking', async () => {
    vi.mocked(api.verifyToken).mockResolvedValue({ valid: true, user: alice })
    const store = useAuthStore()
    await store.ensureSession()
    expect(store.checking).toBe(false)
    expect(store.user).toEqual(alice)
  })

  it('核实网络失败也结束 checking（不能一直占位）', async () => {
    vi.mocked(api.verifyToken).mockRejectedValue(new Error('Network Error'))
    const store = useAuthStore()
    await store.ensureSession()
    expect(store.checking).toBe(false)
  })

  it('核实未返回前已登录：登录结果即权威，立即结束 checking', async () => {
    vi.mocked(api.verifyToken).mockReturnValue(new Promise(() => {}))
    vi.mocked(api.login).mockResolvedValue({ user: alice })
    const store = useAuthStore()
    store.initSession()
    await store.login('a@test.com', 'pw')
    expect(store.checking).toBe(false)
  })
})

describe('管理页路由守卫等待会话核实', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
  })

  it('刷新直达 /admin：核实为管理员后放行', async () => {
    vi.mocked(api.verifyToken).mockResolvedValue({
      valid: true, user: { ...alice, is_admin: true }
    })
    const router = (await import('@/router')).default
    await router.push('/forbidden') // 路由单例：先离开 /admin，避免重复导航跳过守卫
    await router.push('/admin')
    expect(router.currentRoute.value.name).toBe('admin')
  })

  it('非管理员访问 /admin 回首页', async () => {
    vi.mocked(api.verifyToken).mockResolvedValue({ valid: true, user: alice })
    const router = (await import('@/router')).default
    await router.push('/forbidden') // 路由单例：先离开 /admin，避免重复导航跳过守卫
    await router.push('/admin')
    expect(router.currentRoute.value.name).toBe('home')
  })
})
