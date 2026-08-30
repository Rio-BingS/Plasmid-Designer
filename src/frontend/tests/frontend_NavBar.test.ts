import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import NavBar from '@/components/NavBar.vue'
import * as api from '@/api'

// Mock API
vi.mock('@/api', () => ({
  getCurrentUser: vi.fn(),
  login: vi.fn(),
  register: vi.fn()
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
    expect(links.length).toBe(4)
    
    expect(links[0].text()).toContain('首页')
    expect(links[1].text()).toContain('设计')
    expect(links[2].text()).toContain('批量设计')
    expect(links[3].text()).toContain('载体库')
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
