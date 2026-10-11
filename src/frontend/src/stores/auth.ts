import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  login as apiLogin, register as apiRegister, logout as apiLogout, verifyToken,
  getSiteConfig, type SiteConfig
} from '@/api'
import { bumpAuthEpoch, currentAuthEpoch, onSessionExpired } from '@/api/session'

// 旧版本把令牌与用户信息存在 localStorage（XSS 可直接读走）；
// 现在令牌只在后端下发的 httpOnly Cookie 里，启动时清掉遗留数据
const LEGACY_STORAGE_KEYS = ['token', 'user']

// 窗口聚焦/切回标签页的监听全局只装一次，回调指向最近一次 initSession
// 的 store（与 session.ts 的失效回调同一「后注册者为准」约定）
let focusRevalidate: (() => void) | null = null
let focusListenersInstalled = false

function installFocusListeners() {
  if (focusListenersInstalled || typeof window === 'undefined') return
  focusListenersInstalled = true
  const onActive = () => {
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
    focusRevalidate?.()
  }
  window.addEventListener('focus', onActive)
  document.addEventListener('visibilitychange', onActive)
}

export const useAuthStore = defineStore('auth', () => {
  // 登录态唯一来源：后端 /auth/verify（凭 httpOnly 会话 Cookie）或登录响应
  const user = ref<any>(null)
  // 站点配置：未加载完成时视为全开放（导航不闪空），加载后按配置收紧
  const siteConfig = ref<SiteConfig | null>(null)
  // 启动时的会话核实（/auth/verify）尚未有结果：此时既不能断定已登录也不能
  // 断定未登录，导航栏据此显示占位，避免刷新时先闪「登录」再变成用户菜单
  const checking = ref(true)
  // 页面打开期间会话失效（过期 / 他处登出 / 改密）：导航栏据此提示重新登录
  const sessionExpired = ref(false)

  const isAuthenticated = computed(() => !!user.value)
  const isAdmin = computed(() => user.value?.is_admin === true)
  const username = computed(() => user.value?.username || '')

  // 当前用户可见的功能：后端 site-config 的 effective_features 已按
  // 「管理员全量 → 个人权限覆盖 → 层级默认」解析好，直接采用
  const effectiveFeatures = computed(() => {
    if (!siteConfig.value) return null
    return siteConfig.value.effective_features
  })

  const registrationOpen = computed(() => siteConfig.value?.registration_open ?? true)
  const emailVerificationRequired = computed(() => siteConfig.value?.email_verification_required ?? false)

  function featureAllowed(key: string): boolean {
    if (!siteConfig.value) return true
    if (isAdmin.value) return true
    return (effectiveFeatures.value ?? []).includes(key)
  }

  async function refreshSiteConfig(): Promise<void> {
    try {
      siteConfig.value = await getSiteConfig()
    } catch {
      // 后端不可达/未升级版本：保持 null（视为全开放），不打断页面
    }
  }

  /** 建立新会话（登录/注册/邮箱验证成功后；Cookie 已由后端写入） */
  function setSession(sessionUser: any) {
    bumpAuthEpoch()
    user.value = sessionUser
    sessionExpired.value = false
    checking.value = false // 登录响应即权威结果，无需再等启动核实
    refreshSiteConfig() // 登录后层级变化，刷新有效功能
  }

  async function login(email: string, password: string) {
    const result = await apiLogin(email, password)
    setSession(result.user)
    return result
  }

  async function register(email: string, usernameVal: string, password: string, confirmPassword: string) {
    const result = await apiRegister(email, usernameVal, password, confirmPassword)
    if (result.requires_verification) {
      // 邮箱验证流程：尚未登录，由调用方引导输入验证码
      return result
    }
    setSession(result.user)
    return result
  }

  /**
   * 登出：服务端吊销令牌并删除 httpOnly Cookie（前端自己删不掉）。
   * 接口失败（429/5xx/断网）时 Cookie 仍在、会话仍有效：此时不清本地
   * 状态、把错误抛给调用方提示重试——否则界面显示已登出，刷新或下一位
   * 使用者打开页面会话就恢复了（审查 01 M-1）
   */
  async function logout() {
    await apiLogout()
    clearAuth()
    refreshSiteConfig()
  }

  /** 向后端核实会话：有效则同步用户信息，确认无效则清理登录态 */
  async function checkAuth(): Promise<boolean> {
    const epoch = currentAuthEpoch()
    try {
      const result = await verifyToken()
      // 核实期间会话已切换（如用户刚登录）：以新会话为准，不覆盖
      if (epoch !== currentAuthEpoch()) return isAuthenticated.value
      if (result.valid && result.user) {
        user.value = result.user
        return true
      }
      // 原本已登录、复查却无效：会话中途失效（可选认证接口不会 401，
      // 只能靠复查发现），按过期处理并提示重新登录
      if (isAuthenticated.value) expireSession()
      else clearAuth()
      return false
    } catch {
      // 网络错误/后端不可达：无法判断，保持现状（Cookie 可能仍有效）
      return isAuthenticated.value
    }
  }

  // 启动时只核实一次会话；路由守卫（如管理页）可等待同一个结果
  let sessionReady: Promise<boolean> | null = null

  function ensureSession(): Promise<boolean> {
    if (!sessionReady) {
      sessionReady = checkAuth().finally(() => {
        checking.value = false
      })
    }
    return sessionReady
  }

  /** 应用启动：清理旧版本遗留的本地令牌，按 Cookie 恢复会话并加载站点配置 */
  function initSession() {
    try {
      for (const key of LEGACY_STORAGE_KEYS) localStorage.removeItem(key)
    } catch {
      // 隐身模式等 localStorage 不可用时忽略
    }
    ensureSession().finally(refreshSiteConfig)
    focusRevalidate = () => { revalidateSession() }
    installFocusListeners()
  }

  // 回到页面时（切回标签页/窗口聚焦）复查会话：令牌 24h 过期或在别处登出后，
  // 走可选认证的接口会静默按匿名执行而不 401，单靠拦截器发现不了
  const REVALIDATE_MIN_INTERVAL_MS = 60_000
  let lastRevalidate = 0

  /** 已登录时节流复查一次会话；未登录或间隔太短时不发请求 */
  function revalidateSession(): Promise<boolean> {
    if (!isAuthenticated.value) return Promise.resolve(false)
    const now = Date.now()
    if (now - lastRevalidate < REVALIDATE_MIN_INTERVAL_MS) return Promise.resolve(true)
    lastRevalidate = now
    return checkAuth()
  }

  function clearAuth() {
    bumpAuthEpoch()
    user.value = null
  }

  /** 会话被动失效：清理登录态、按匿名刷新功能集，并提示重新登录 */
  function expireSession() {
    const wasAuthenticated = isAuthenticated.value
    clearAuth()
    if (!wasAuthenticated) return
    sessionExpired.value = true
    refreshSiteConfig()
  }

  function dismissSessionExpired() {
    sessionExpired.value = false
  }

  // 接口返回 401（当前会话已失效）时由 api 拦截器经 session 模块回调清理
  onSessionExpired(expireSession)

  return {
    user,
    siteConfig,
    checking,
    sessionExpired,
    isAuthenticated,
    isAdmin,
    username,
    effectiveFeatures,
    registrationOpen,
    emailVerificationRequired,
    featureAllowed,
    refreshSiteConfig,
    setSession,
    login,
    register,
    logout,
    checkAuth,
    ensureSession,
    initSession,
    revalidateSession,
    dismissSessionExpired,
    clearAuth
  }
})
