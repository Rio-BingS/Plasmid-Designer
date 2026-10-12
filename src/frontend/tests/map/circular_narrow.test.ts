import { describe, it, expect, vi } from 'vitest'
import { ref } from 'vue'
import { mount } from '@vue/test-utils'
import type { MapDocument } from '@/map/types'
import pUC19 from '../fixtures/map/pUC19.json'

// 模拟手机结果页卡片里的窄容器（~260 px）
vi.mock('@vueuse/core', () => ({ useElementSize: () => ({ width: ref(260), height: ref(0) }) }))

import CircularMap from '@/map/components/CircularMap.vue'

describe('CircularMap 窄容器', () => {
  it('按实际宽度排版，而不是先按 300 px 排再被 CSS 缩小', () => {
    const w = mount(CircularMap, { props: { doc: JSON.parse(JSON.stringify(pUC19)) as MapDocument } })
    expect(Number(w.find('.cmap-svg').attributes('width'))).toBe(260)
    expect(w.html()).not.toMatch(/NaN/)
  })
})
