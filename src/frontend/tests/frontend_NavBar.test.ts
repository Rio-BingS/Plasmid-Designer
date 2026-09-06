import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import NavBar from '@/components/NavBar.vue'
import * as api from '@/api'

// Mock API —— NavBar 挂载时 authStore.initFromStorage() 会调用 verifyToken，
// 必须提供有效返回，否则 checkAuth 走 clearAuth 分支清空登录态
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
        anonymous: ['design', 'batch', 'vectors', 'sequencing', 'analysis', 'codon'],
        user: ['design', 'batch', 'vectors', 'sequencing', 'analysis', 'codon']
      },
      effective_features: ['design', 'batch', 'vectors', 'sequencing', 'analysis', 'codon']
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
    const wrapper = mount(NavBar, {
      global: {
        plugins: [router],
        stubs: {
          AuthModal: true
        }
      }
    })
    await router.isReady()
    
    expect(wrapper.find('.login-btn').exists()).toBe(true)
    expect(wrapper.find('.login-btn').text()).toContain('登录')
  })

  it('shows user info when logged in', async () => {
    localStorage.setItem('user', JSON.stringify({ username: 'testuser', email: 'test@example.com' }))
    localStorage.setItem('token', 'fake-token')
    
    const wrapper = mount(NavBar, {
      global: {
        plugins: [router],
        stubs: {
          AuthModal: true
        }
      }
    })
    await router.isReady()
    
    expect(wrapper.find('.user-btn').exists()).toBe(true)
    expect(wrapper.find('.user-name').text()).toBe('testuser')
  })

  it('shows user dropdown menu when clicking user button', async () => {
    localStorage.setItem('user', JSON.stringify({ username: 'testuser', email: 'test@example.com' }))
    localStorage.setItem('token', 'fake-token')
    
    const wrapper = mount(NavBar, {
      global: {
        plugins: [router],
        stubs: {
          AuthModal: true
        }
      }
    })
    await router.isReady()
    
    await wrapper.find('.user-btn').trigger('click')
    expect(wrapper.find('.user-dropdown').isVisible()).toBe(true)
  })

  it('clears localStorage on logout', async () => {
    localStorage.setItem('user', JSON.stringify({ username: 'testuser' }))
    localStorage.setItem('token', 'fake-token')
    
    const wrapper = mount(NavBar, {
      global: {
        plugins: [router],
        stubs: {
          AuthModal: true
        }
      }
    })
    await router.isReady()
    
    await wrapper.find('.user-btn').trigger('click')
    await wrapper.find('.logout-btn').trigger('click')
    
    expect(localStorage.getItem('user')).toBeNull()
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
