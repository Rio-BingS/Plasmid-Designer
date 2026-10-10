import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import NavBar from '@/components/NavBar.vue'
import * as api from '@/api'

// Mock API —— NavBar 挂载时 authStore.initSession() 调用 verifyToken 按会话
// Cookie 恢复登录态（前端不再保存令牌），默认返回已登录用户
vi.mock('@/api', () => ({
  getCurrentUser: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
  verifyToken: vi.fn(() =>
    Promise.resolve({ valid: true, user: { username: 'testuser', email: 'test@example.com' } })
  ),
  logout: vi.fn(),
  // 站点配置：默认全开放（未加载完成/默认态导航显示全部入口）
  getSiteConfig: vi.fn(() =>
    Promise.resolve({
      registration_open: true,
      email_verification_required: false,
      tier: 'anonymous',
      features: {
        anonymous: ['design', 'batch', 'vectors', 'sequencing', 'sequencing_batch', 'analysis', 'codon'],
        user: ['design', 'batch', 'vectors', 'sequencing', 'sequencing_batch', 'analysis', 'codon']
      },
      effective_features: ['design', 'batch', 'vectors', 'sequencing', 'sequencing_batch', 'analysis', 'codon']
    })
  )
}))

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: { template: '<div>Home</div>' } },
    { path: '/design', component: { template: '<div>Design</div>' } },
    { path: '/batch', component: { template: '<div>Batch</div>' } },
    { path: '/vectors', component: { template: '<div>Vectors</div>' } }
  ]
})

describe('NavBar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('renders navigation links', async () => {
    const wrapper = mount(NavBar, {
      global: {
        plugins: [router],
        stubs: {
          AuthModal: true
        }
      }
    })
    await router.isReady()
    
    const links = wrapper.findAll('.nav-link')
    expect(links.length).toBe(7)

    expect(links[0].text()).toContain('首页')
    expect(links[1].text()).toContain('设计')
    expect(links[2].text()).toContain('批量设计')
    expect(links[3].text()).toContain('载体库')
    expect(links[4].text()).toContain('测序分析')
    expect(links[5].text()).toContain('批量测序')
    expect(links[6].text()).toContain('序列工具')
  })

  it('shows login button when user is not logged in', async () => {
    vi.mocked(api.verifyToken).mockResolvedValueOnce({ valid: false, user: null })
    const wrapper = mount(NavBar, {
      global: {
        plugins: [router],
        stubs: {
          AuthModal: true
        }
      }
    })
    await router.isReady()
    await flushPromises()

    expect(wrapper.find('.login-btn').exists()).toBe(true)
    expect(wrapper.find('.login-btn').text()).toContain('登录')
  })

  it('shows user info when logged in', async () => {
    const wrapper = mount(NavBar, {
      global: {
        plugins: [router],
        stubs: {
          AuthModal: true
        }
      }
    })
    await router.isReady()
    await flushPromises()

    expect(wrapper.find('.user-btn').exists()).toBe(true)
    expect(wrapper.find('.user-name').text()).toBe('testuser')
  })

  it('shows user dropdown menu when clicking user button', async () => {
    const wrapper = mount(NavBar, {
      global: {
        plugins: [router],
        stubs: {
          AuthModal: true
        }
      }
    })
    await router.isReady()
    await flushPromises()

    await wrapper.find('.user-btn').trigger('click')
    expect(wrapper.find('.user-dropdown').isVisible()).toBe(true)
  })

  it('logs out via server and returns to logged-out state', async () => {
    const wrapper = mount(NavBar, {
      global: {
        plugins: [router],
        stubs: {
          AuthModal: true
        }
      }
    })
    await router.isReady()
    await flushPromises()

    await wrapper.find('.user-btn').trigger('click')
    await wrapper.find('.logout-btn').trigger('click')
    await flushPromises()

    // 服务端吊销令牌并删除 httpOnly Cookie；前端回到未登录态
    expect(api.logout).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.login-btn').exists()).toBe(true)
    expect(localStorage.getItem('token')).toBeNull()
  })

  it('renders brand logo and text', async () => {
    const wrapper = mount(NavBar, {
      global: {
        plugins: [router],
        stubs: {
          AuthModal: true
        }
      }
    })
    await router.isReady()
    
    expect(wrapper.find('.logo').text()).toBe('🧬')
    expect(wrapper.find('.brand-text').text()).toBe('Plasmid Designer')
  })
})
