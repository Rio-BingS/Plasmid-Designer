import { config } from '@vue/test-utils'

// 全局配置
config.global.stubs = {}

// 说明：happy-dom 自带真实 localStorage，此处不要用空实现 mock 覆盖，
// 否则依赖「存入后可读出」的用例（如 api 存取 token）会全部失败。

// 模拟 URL.createObjectURL（happy-dom 未实现，质粒图谱导出会用到）
globalThis.URL.createObjectURL = vi.fn(() => 'blob:test-url')
globalThis.URL.revokeObjectURL = vi.fn()
