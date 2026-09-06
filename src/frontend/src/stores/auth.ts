import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  login as apiLogin, register as apiRegister, logout as apiLogout, verifyToken,
  getSiteConfig, type SiteConfig
} from '@/api'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<any>(null)
  const token = ref<string | null>(null)
  // 站点配置：未加载完成时视为全开放（导航不闪空），加载后按配置收紧
  const siteConfig = ref<SiteConfig | null>(null)

  const isAuthenticated = computed(() => !!user.value && !!token.value)
  const isAdmin = computed(() => user.value?.is_admin === true)
  const username = computed(() => user.value?.username || '')

  // 当前层级可见的功能（按层级自行计算，管理员全量）；管理员用 user 清单做 UI 展示
  const effectiveFeatures = computed(() => {
    if (!siteConfig.value) return null
    return siteConfig.value.features.user
  })

  const registrationOpen = computed(() => siteConfig.value?.registration_open ?? true)
  const emailVerificationRequired = computed(() => siteConfig.value?.email_verification_required ?? false)

  function featureAllowed(key: string): boolean {
    if (!siteConfig.value) return true
    if (isAdmin.value) return true
    const tier = isAuthenticated.value ? 'user' : 'anonymous'
    const list = tier === 'user' ? siteConfig.value.features.user : siteConfig.value.features.anonymous
    return (list ?? []).includes(key)
  }

  async function refreshSiteConfig(): Promise<void> {
    try {
      siteConfig.value = await getSiteConfig()
    } catch {
      // 后端不可达/未升级版本：保持 null（视为全开放），不打断页面
    }
  }

  async function login(email: string, password: string) {
    const result = await apiLogin(email, password)
    token.value = result.access_token
    user.value = result.user
    localStorage.setItem('token', result.access_token)
    localStorage.setItem('user', JSON.stringify(result.user))
    refreshSiteConfig() // 登录后层级变化，刷新有效功能
    return result
  }

  async function register(email: string, usernameVal: string, password: string, confirmPassword: string) {
    const result = await apiRegister(email, usernameVal, password, confirmPassword)
    if (result.requires_verification) {
      // 邮箱验证流程：不落本地登录态，由调用方引导输入验证码
      return result
    }
    token.value = result.access_token
    user.value = result.user
    localStorage.setItem('token', result.access_token)
    localStorage.setItem('user', JSON.stringify(result.user))
    refreshSiteConfig()
    return result
  }

  async function logout() {
    try {
      await apiLogout()
    } catch {
      // 即使后端登出失败，也要清理本地状态
    }
    token.value = null
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    refreshSiteConfig()
  }

  async function checkAuth(): Promise<boolean> {
    if (!token.value) {
      clearAuth()
      return false
    }
    try {
      const result = await verifyToken()
      if (result.valid && result.user) {
        user.value = result.user
        localStorage.setItem('user', JSON.stringify(result.user))
        return true
      } else {
        clearAuth()
        return false
      }
    } catch {
      clearAuth()
      return false
    }
  }

  function initFromStorage() {
    const storedToken = localStorage.getItem('token')
    const storedUser = localStorage.getItem('user')
    if (storedToken && storedUser) {
      token.value = storedToken
      try {
        user.value = JSON.parse(storedUser)
      } catch {
        clearAuth()
      }
    }
    // 异步验证 token 有效性与站点配置
    if (token.value) {
      checkAuth().finally(refreshSiteConfig)
    } else {
      refreshSiteConfig()
    }
  }

  function clearAuth() {
    token.value = null
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  }

  return {
    user,
    token,
    siteConfig,
    isAuthenticated,
    isAdmin,
    username,
    effectiveFeatures,
    registrationOpen,
    emailVerificationRequired,
    featureAllowed,
    refreshSiteConfig,
    login,
    register,
    logout,
    checkAuth,
    initFromStorage,
    clearAuth
  }
})
