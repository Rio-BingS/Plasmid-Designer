/** 站点功能门控（auth store）与管理面板测试 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'
import NavBar from '@/components/NavBar.vue'
import ForbiddenView from '@/views/ForbiddenView.vue'
import * as api from '@/api'

const fullFeatures = ['design', 'batch', 'vectors', 'sequencing', 'analysis', 'codon']

function siteConfig(overrides: Partial<api.SiteConfig> = {}): api.SiteConfig {
  return {
    registration_open: true,
    email_verification_required: false,
    tier: 'anonymous',
    features: { anonymous: fullFeatures, user: fullFeatures },
    effective_features: fullFeatures,
    ...overrides
  }
}

vi.mock('@/api', () => ({
  getCurrentUser: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
  verifyToken: vi.fn(() => Promise.resolve({ valid: false, user: null })),
  logout: vi.fn(),
  getSiteConfig: vi.fn(),
  verifyEmail: vi.fn(),
  resendVerification: vi.fn()
}))

describe('auth store 功能门控', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('站点配置未加载时放行所有功能（导航不闪空）', () => {
    const store = useAuthStore()
    expect(store.featureAllowed('design')).toBe(true)
    expect(store.registrationOpen).toBe(true)
  })

  it('匿名用户按 anonymous_features 过滤', async () => {
    vi.mocked(api.getSiteConfig).mockResolvedValue(
      siteConfig({ features: { anonymous: ['vectors'], user: fullFeatures } })
    )
    const store = useAuthStore()
    await store.refreshSiteConfig()
    expect(store.featureAllowed('vectors')).toBe(true)
    expect(store.featureAllowed('design')).toBe(false)
    expect(store.featureAllowed('sequencing')).toBe(false)
  })

  it('登录用户按 user_features 过滤', async () => {
    vi.mocked(api.getSiteConfig).mockResolvedValue(
      siteConfig({ features: { anonymous: [], user: ['design', 'vectors'] } })
    )
    const store = useAuthStore()
    store.user = { username: 'u', is_admin: false }
    store.token = 't'
    await store.refreshSiteConfig()
    expect(store.featureAllowed('design')).toBe(true)
    expect(store.featureAllowed('batch')).toBe(false)
  })

  it('管理员不受功能开关限制', async () => {
    vi.mocked(api.getSiteConfig).mockResolvedValue(
      siteConfig({ features: { anonymous: [], user: [] } })
    )
    const store = useAuthStore()
    store.user = { username: 'boss', is_admin: true }
    store.token = 't'
    await store.refreshSiteConfig()
    expect(store.isAdmin).toBe(true)
    expect(store.featureAllowed('design')).toBe(true)
    expect(store.featureAllowed('batch')).toBe(true)
  })
})

describe('NavBar 按功能开关过滤导航', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    localStorage.clear()
  })

  async function mountNav(config: api.SiteConfig | null) {
    vi.mocked(api.getSiteConfig).mockResolvedValue(config ?? siteConfig())
    const wrapper = mount(NavBar, {
      global: { plugins: [(await import('@/router')).default], stubs: { AuthModal: true } }
    })
    await flushPromises()
    return wrapper
  }

  it('全开放时显示全部 7 个导航项', async () => {
    const wrapper = await mountNav(siteConfig())
    expect(wrapper.findAll('.nav-link').length).toBe(7)
  })

  it('匿名用户只看到开放的功能入口', async () => {
    const wrapper = await mountNav(
      siteConfig({ features: { anonymous: ['vectors', 'design'], user: fullFeatures } })
    )
    const links = wrapper.findAll('.nav-link').map((l) => l.text())
    expect(links).toContain('设计')
    expect(links).toContain('载体库')
    expect(links).not.toContain('批量设计')
    expect(links).not.toContain('测序分析')
  })
})

describe('ForbiddenView', () => {
  it('展示未开放功能名与提示', async () => {
    setActivePinia(createPinia())
    const { createRouter, createMemoryHistory } = await import('vue-router')
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/forbidden', component: ForbiddenView }]
    })
    router.push('/forbidden?feature=sequencing')
    await router.isReady()
    const wrapper = mount(ForbiddenView, { global: { plugins: [router] } })
    expect(wrapper.text()).toContain('测序分析未开放')
    expect(wrapper.text()).toContain('联系管理员')
  })
})
