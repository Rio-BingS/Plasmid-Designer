/** 路由级拆包（审查 03-Low 8）：图谱相关页面按需加载，主包不再带整个图谱模块 */
import { describe, it, expect, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

vi.mock('@/api', async (orig) => ({ ...(await orig<typeof import('@/api')>()), getSiteConfig: vi.fn().mockResolvedValue(null) }))

import router from '@/router'

describe('路由拆包', () => {
  it('除首页 / 403 外的页面都是动态导入（函数而不是组件对象）', () => {
    const byName = Object.fromEntries(router.getRoutes().map((r) => [String(r.name), r.components?.default]))
    for (const name of ['map', 'vectors', 'vector-detail', 'result', 'sequencing', 'sequencing-batch', 'design', 'batch', 'analysis', 'cache', 'admin']) {
      expect(typeof byName[name], name).toBe('function')
    }
    expect(typeof byName.home).toBe('object')
  })

  it('路由表与结果页不再静态导入图谱模块', () => {
    const routerSrc = readFileSync(resolve(__dirname, '../src/router/index.ts'), 'utf8')
    expect(routerSrc).not.toMatch(/^import .*@\/views\/(MapView|VectorsView|VectorDetailView|ResultView)\.vue/m)
    const resultSrc = readFileSync(resolve(__dirname, '../src/views/ResultView.vue'), 'utf8')
    expect(resultSrc).not.toMatch(/^import MapViewer from/m)
  })

  it('分包加载失败（重新部署后旧标签页）时整页跳转一次拿新版本，不死循环', async () => {
    setActivePinia(createPinia())
    const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => {})
    const errs: unknown[] = []
    router.onError((e) => errs.push(e))
    sessionStorage.clear()
    const broken = { path: '/__broken', name: '__broken', component: () => Promise.reject(new TypeError('Failed to fetch dynamically imported module: /assets/x.js')) }
    router.addRoute(broken)
    await router.push('/__broken').catch(() => {})
    await router.push('/').catch(() => {})
    await router.push('/__broken').catch(() => {})
    expect(assign).toHaveBeenCalledTimes(1)
    expect(assign).toHaveBeenCalledWith('/__broken')
    expect(errs.length).toBe(2)
    router.removeRoute('__broken')
    assign.mockRestore()
  })
})
