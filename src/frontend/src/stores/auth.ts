import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  login as apiLogin, register as apiRegister, logout as apiLogout, verifyToken,
  getSiteConfig, type SiteConfig
} from '@/api'
import { bumpAuthEpoch, currentAuthEpoch } from '@/api/session'

// 旧版本把令牌与用户信息存在 localStorage（XSS 可直接读走）；
// 现在令牌只在后端下发的 httpOnly Cookie 里，启动时清掉遗留数据
const LEGACY_STORAGE_KEYS = ['token', 'user']

export const useAuthStore = defineStore('auth', () => {
  // 登录态唯一来源：后端 /auth/verify（凭 httpOnly 会话 Cookie）或登录响应
  const user = ref<any>(null)
  // 站点配置：未加载完成时视为全开放（导航不闪空），加载后按配置收紧
  const siteConfig = ref<SiteConfig | null>(null)

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

  async function logout() {
    try {
      // 服务端吊销令牌并删除 httpOnly Cookie（前端自己删不掉）
      await apiLogout()
    } catch {
      // 即使后端登出失败，也要清理本地状态
    }
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
      clearAuth()
      return false
    } catch {
      // 网络错误/后端不可达：无法判断，保持现状（Cookie 可能仍有效）
      return isAuthenticated.value
    }
  }

  // 启动时只核实一次会话；路由守卫（如管理页）可等待同一个结果
  let sessionReady: Promise<boolean> | null = null

  function ensureSession(): Promise<boolean> {
    if (!sessionReady) sessionReady = checkAuth()
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
  }

  function clearAuth() {
    bumpAuthEpoch()
    user.value = null
  }

  return {
    user,
    siteConfig,
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
    clearAuth
  }
})
