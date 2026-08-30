import { config } from '@vue/test-utils'

// 全局配置
config.global.stubs = {}

// 模拟 localStorage
const localStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn()
}
Object.defineProperty(window, 'localStorage', { value: localStorageMock })

// 模拟 URL.createObjectURL
globalThis.URL.createObjectURL = vi.fn(() => 'blob:test-url')
globalThis.URL.revokeObjectURL = vi.fn()
