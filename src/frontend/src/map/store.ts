/**
 * 图谱共享状态：一个 MapViewer 内的环形/线性/序列/缩略视图共用同一份
 * 选区、悬停、显示开关、酶过滤、搜索结果与导航请求（Benchling 式联动）。
 * 用 provide/inject 按查看器隔离，页面上可并存多个互不干扰的查看器。
 */
import { computed, inject, provide, reactive, ref, shallowRef, watch, type InjectionKey, type Ref } from 'vue'
import type { MapDocument, MapFeature, Range } from './types'
import { rangeLength, rangeMid } from './core/coords'
import { cutMarks, defaultEnzymeFilter, listEnzymes, visibleEnzymes, type EnzymeFilter } from './core/enzymes'
import { searchSequence, type SearchHit } from './core/search'
import { selectionStats } from './core/seq'

export type ViewMode = 'circular' | 'linear' | 'sequence' | 'split'

export interface HoverInfo {
  kind: 'feature' | 'enzyme' | 'orf' | 'primer' | 'track' | 'search'
  id: string
  title: string
  lines: string[]
  range?: Range | null
  /** 视口坐标（clientX/Y），提示框定位 */
  x: number
  y: number
}

/** 视图里没能显示的项（标签放不下/超出分道上限），汇总到"N 项无法显示" */
export interface UnshownItem { id: string; name: string; reason: string }

export function createMapStore(docRef: Ref<MapDocument>) {
  const doc = docRef
  const L = computed(() => doc.value.length)
  const circular = computed(() => doc.value.topology === 'circular')

  const selection = ref<Range | null>(null)
  /** 光标（边界坐标 0..L），无选区时显示 */
  const caret = ref<number | null>(null)
  const selectedFeatureId = ref<string | null>(null)
  const hover = shallowRef<HoverInfo | null>(null)
  /**
   * 悬停的元件（id + 区间），只在悬停对象变化时才变：各视图的高亮读它，
   * 指针在同一元件上移动（只改 x/y）不触发高亮/标签重算；提示框定位仍读 hover。
   */
  const hoverTarget = computed<{ id: string; range: Range | null } | null>((prev) => {
    const h = hover.value
    if (!h) return null
    const range = h.range ?? null
    if (prev && prev.id === h.id && (prev.range === range
      || (!!prev.range && !!range && prev.range.start === range.start && prev.range.end === range.end))) return prev
    return { id: h.id, range }
  })

  const show = reactive({
    features: true, enzymes: true, orfs: false, primers: true, labels: true,
    complement: true, translations: true,
    /** 显示被 dam/dcm 甲基化阻断的位点（灰色虚线） */
    blocked: true,
  })
  const hiddenFeatures = ref<string[]>([])
  const enzymeFilter = reactive<EnzymeFilter>(defaultEnzymeFilter())

  const search = reactive({
    query: '', mismatches: 0, hits: [] as SearchHit[], index: -1, error: null as string | null, truncated: false,
  })

  /** 环形图旋转（弧度） */
  const rotation = ref(0)
  /** 线性视口（边界坐标） */
  const viewport = ref<{ from: number; to: number }>({ from: 0, to: doc.value.length })
  /** 序列视图当前可见区（1-based），供缩略导航显示 */
  const seqVisible = ref<Range | null>(null)
  /** 导航请求：序列视图滚到 pos，线性视图把 range 放进视口（n 自增触发 watch） */
  const navigate = ref<{ pos: number; range: Range | null; n: number }>({ pos: 1, range: null, n: 0 })
  const unshown = reactive<Record<string, UnshownItem[]>>({})

  watch(() => doc.value, (d, old) => {
    if (old && d.length === old.length && d.sequence === old.sequence) return
    selection.value = null
    caret.value = null
    selectedFeatureId.value = null
    viewport.value = { from: 0, to: d.length }
    search.hits = []
    search.index = -1
  })

  const features = computed<MapFeature[]>(() => {
    if (!show.features) return []
    const hidden = new Set(hiddenFeatures.value)
    return doc.value.features.filter((f) => !hidden.has(f.id))
  })
  const enzymeList = computed(() => listEnzymes(doc.value.enzymes, enzymeFilter))
  const enzymes = computed(() => (show.enzymes ? visibleEnzymes(doc.value.enzymes, enzymeFilter) : []))
  const cuts = computed(() => cutMarks(enzymes.value, show.blocked))
  const orfs = computed(() => (show.orfs ? doc.value.orfs : []))
  const primers = computed(() => (show.primers ? doc.value.primers : []))
  const selectedFeature = computed(() => doc.value.features.find((f) => f.id === selectedFeatureId.value) ?? null)

  const stats = computed(() => {
    if (!selection.value || !doc.value.sequence) return null
    return selectionStats(doc.value.sequence, selection.value)
  })

  function select(r: Range | null, opts: { featureId?: string | null; navigate?: boolean } = {}) {
    selection.value = r
    selectedFeatureId.value = opts.featureId ?? null
    if (r) caret.value = r.start - 1
    if (r && opts.navigate) navigateTo(r)
  }

  function setCaret(x: number | null) {
    selection.value = null
    selectedFeatureId.value = null
    caret.value = x
  }

  function selectFeature(id: string, nav = true) {
    const f = doc.value.features.find((x) => x.id === id)
    if (!f) return
    select({ start: f.start, end: f.end }, { featureId: id, navigate: nav })
  }

  function navigateTo(r: Range | number) {
    const range = typeof r === 'number' ? null : r
    const pos = typeof r === 'number' ? r : r.start
    navigate.value = { pos, range, n: navigate.value.n + 1 }
  }

  function toggleFeatureHidden(id: string) {
    const s = new Set(hiddenFeatures.value)
    if (s.has(id)) s.delete(id)
    else s.add(id)
    hiddenFeatures.value = [...s]
  }

  function toggleEnzymeHidden(name: string) {
    const s = new Set(enzymeFilter.hidden)
    if (s.has(name)) s.delete(name)
    else s.add(name)
    enzymeFilter.hidden = [...s]
  }

  function runSearch(query = search.query, mismatches = search.mismatches) {
    search.query = query
    search.mismatches = mismatches
    const res = searchSequence(doc.value.sequence, query, { maxMismatches: mismatches, circular: circular.value })
    search.hits = res.hits
    search.error = res.error
    search.truncated = res.truncated
    search.index = res.hits.length ? 0 : -1
    if (res.hits.length) focusHit(0)
  }

  function focusHit(i: number) {
    const n = search.hits.length
    if (!n) return
    search.index = ((i % n) + n) % n
    const h = search.hits[search.index]
    select({ start: h.start, end: h.end }, { navigate: true })
  }

  function clearSearch() {
    search.query = ''
    search.hits = []
    search.index = -1
    search.error = null
  }

  function setUnshown(view: string, items: UnshownItem[]) {
    unshown[view] = items
  }

  /** 选区或悬停元件的长度/中点等的便捷读数 */
  function rangeInfo(r: Range) {
    return { length: rangeLength(r, L.value, circular.value), mid: rangeMid(r, L.value) }
  }

  return {
    doc, L, circular, selection, caret, selectedFeatureId, selectedFeature, hover, hoverTarget, show, hiddenFeatures,
    enzymeFilter, search, rotation, viewport, seqVisible, navigate, unshown,
    features, enzymeList, enzymes, cuts, orfs, primers, stats,
    select, setCaret, selectFeature, navigateTo, toggleFeatureHidden, toggleEnzymeHidden,
    runSearch, focusHit, clearSearch, setUnshown, rangeInfo,
  }
}

export type MapStore = ReturnType<typeof createMapStore>

const KEY: InjectionKey<MapStore> = Symbol('plasmid-map-store')

export function provideMapStore(store: MapStore): MapStore {
  provide(KEY, store)
  return store
}

/** 有上层 MapViewer 时用其共享状态，否则按传入文档建本地状态（组件可单独使用） */
export function useMapStore(docGetter: () => MapDocument | undefined | null): { store: MapStore; own: boolean } {
  const injected = inject(KEY, null)
  if (injected) return { store: injected, own: false }
  const d = docGetter()
  if (!d) throw new Error('图谱组件需要 doc 属性或外层 MapViewer')
  const docRef = ref(d) as Ref<MapDocument>
  watch(docGetter, (nd) => { if (nd) docRef.value = nd })
  return { store: createMapStore(docRef), own: true }
}
