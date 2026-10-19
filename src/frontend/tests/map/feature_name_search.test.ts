import { describe, it, expect } from 'vitest'
import { ref } from 'vue'
import type { MapDocument, MapFeature } from '@/map/types'
import { searchFeatures } from '@/map/core/search'
import { createMapStore } from '@/map/store'

const feats: MapFeature[] = [
  { id: 'f1', name: 'PNPM5', type: 'cds', start: 1, end: 30, strand: 1 },
  { id: 'f2', name: 'AmpR', type: 'marker', start: 31, end: 60, strand: -1 },
  { id: 'f3', name: 'pnp-2', type: 'gene', start: 61, end: 90, strand: 1 },
] as unknown as MapFeature[]

const doc = {
  id: 'probe', name: 'probe', topology: 'circular', length: 90,
  sequence: 'ATGGCTGCT'.repeat(10),
  features: feats, primers: [], orfs: [], enzymes: [],
} as unknown as MapDocument

describe('元件名称搜索', () => {
  it('按名称做大小写不敏感的子串匹配', () => {
    expect(searchFeatures(feats, 'pnp').map((f) => f.id)).toEqual(['f1', 'f3'])
    expect(searchFeatures(feats, 'PNPM5').map((f) => f.id)).toEqual(['f1'])
    expect(searchFeatures(feats, 'AMPR').map((f) => f.id)).toEqual(['f2'])
  })

  it('空查询与无匹配返回空数组', () => {
    expect(searchFeatures(feats, '   ')).toEqual([])
    expect(searchFeatures(feats, 'T7')).toEqual([])
  })

  it('按文档顺序返回并带上区间（供选中/定位）', () => {
    const [hit] = searchFeatures(feats, 'ampr')
    expect(hit).toMatchObject({ id: 'f2', name: 'AmpR', start: 31, end: 60 })
  })

  it('limit 截断命中数', () => {
    expect(searchFeatures(feats, 'pnp', 1).map((f) => f.id)).toEqual(['f1'])
  })
})

describe('搜索框：序列与元件名称共用', () => {
  it('查询不是 DNA 时按名称命中并选中该元件', () => {
    const store = createMapStore(ref(doc))
    store.runSearch('PNPM5', 0)
    expect(store.search.mode).toBe('name')
    expect(store.search.hits).toEqual([])
    expect(store.search.featureHits.map((f) => f.id)).toEqual(['f1'])
    expect(store.search.featureIndex).toBe(0)
    expect(store.selectedFeatureId.value).toBe('f1')
    expect(store.selection.value).toEqual({ start: 1, end: 30 })
  })

  it('序列命中优先于名称命中', () => {
    const store = createMapStore(ref(doc))
    store.runSearch('ATGGCT', 0)
    expect(store.search.mode).toBe('sequence')
    expect(store.search.hits.length).toBeGreaterThan(0)
    expect(store.search.featureHits).toEqual([])
  })

  it('名称命中可在多条之间切换', () => {
    const store = createMapStore(ref(doc))
    store.runSearch('pnp', 0)
    expect(store.search.featureHits.map((f) => f.id)).toEqual(['f1', 'f3'])
    store.focusFeatureHit(1)
    expect(store.search.featureIndex).toBe(1)
    expect(store.selectedFeatureId.value).toBe('f3')
    store.focusFeatureHit(-1) // 环绕到最后一个
    expect(store.search.featureIndex).toBe(1)
    store.focusFeatureHit(2)
    expect(store.search.featureIndex).toBe(0)
  })

  it('都没有命中时不选中，也不报“只能包含 DNA 碱基”', () => {
    const store = createMapStore(ref(doc))
    store.runSearch('T7', 0)
    expect(store.search.hits).toEqual([])
    expect(store.search.featureHits).toEqual([])
    expect(store.search.error).toBeNull()
    expect(store.selectedFeatureId.value).toBeNull()
  })

  it('清空搜索会清掉名称命中', () => {
    const store = createMapStore(ref(doc))
    store.runSearch('PNPM5', 0)
    store.clearSearch()
    expect(store.search.featureHits).toEqual([])
    expect(store.search.featureIndex).toBe(-1)
  })
})
