import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import DisclaimerModal from '@/components/DisclaimerModal.vue'

// 弹窗经 <Teleport to="body"> 渲染，测试时 stub 留在组件树内便于断言
const mountModal = () => mount(DisclaimerModal, {
  global: { stubs: { teleport: true } }
})

describe('DisclaimerModal', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('首次访问显示免责声明弹窗', async () => {
    const wrapper = mountModal()
    await wrapper.vm.$nextTick() // visible 在 onMounted 置位，等渲染刷新
    expect(wrapper.find('.disclaimer-overlay').exists()).toBe(true)
    expect(wrapper.text()).toContain('免责声明')
    expect(wrapper.text()).toContain('仅供学习与科研参考')
    expect(wrapper.text()).toContain('严禁用于临床、诊断或生产用途')
  })

  it('点击确认后关闭并写入 localStorage', async () => {
    const wrapper = mountModal()
    await wrapper.vm.$nextTick()
    await wrapper.find('.disclaimer-btn').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.disclaimer-overlay').exists()).toBe(false)
    expect(localStorage.getItem('disclaimer_ack_v1')).toBeTruthy()
  })

  it('已确认过则不再弹出', () => {
    localStorage.setItem('disclaimer_ack_v1', '2026-09-06T00:00:00.000Z')
    const wrapper = mountModal()
    expect(wrapper.find('.disclaimer-overlay').exists()).toBe(false)
  })

  it('暴露 open() 可重新打开（页脚入口）', async () => {
    localStorage.setItem('disclaimer_ack_v1', '2026-09-06T00:00:00.000Z')
    const wrapper = mountModal()
    expect(wrapper.find('.disclaimer-overlay').exists()).toBe(false)
    ;(wrapper.vm as any).open()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.disclaimer-overlay').exists()).toBe(true)
  })
})
