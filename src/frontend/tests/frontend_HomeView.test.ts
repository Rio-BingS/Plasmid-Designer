import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: { template: '<div>Home</div>' } },
    { path: '/design', component: { template: '<div>Design</div>' } }
  ]
})

describe('HomeView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders hero section with title', async () => {
    const wrapper = mount(HomeView, {
      global: {
        plugins: [router]
      }
    })
    await router.isReady()
    
    expect(wrapper.find('h1').text()).toBe('🧬 Plasmid Designer')
    expect(wrapper.find('.hero-subtitle').text()).toContain('自动化质粒构建设计平台')
  })

  it('renders feature cards', async () => {
    const wrapper = mount(HomeView, {
      global: {
        plugins: [router]
      }
    })
    await router.isReady()
    
    const featureCards = wrapper.findAll('.feature-card')
    expect(featureCards.length).toBe(4)
  })

  it('renders workflow steps', async () => {
    const wrapper = mount(HomeView, {
      global: {
        plugins: [router]
      }
    })
    await router.isReady()
    
    const steps = wrapper.findAll('.step')
    expect(steps.length).toBe(4)
  })

  it('has correct workflow step titles', async () => {
    const wrapper = mount(HomeView, {
      global: {
        plugins: [router]
      }
    })
    await router.isReady()
    
    const stepContents = wrapper.findAll('.step-content h4')
    const titles = stepContents.map(s => s.text())
    
    expect(titles).toContain('输入序列')
    expect(titles).toContain('选择载体和方法')
    expect(titles).toContain('自动设计')
    expect(titles).toContain('下载结果')
  })

  it('has link to design page', async () => {
    const wrapper = mount(HomeView, {
      global: {
        plugins: [router]
      }
    })
    await router.isReady()
    
    const designLink = wrapper.find('.hero-btn')
    expect(designLink.exists()).toBe(true)
  })
})
