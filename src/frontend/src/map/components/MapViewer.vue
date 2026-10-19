<script setup lang="ts">
/**
 * 图谱查看器：工具栏（视图切换、图层开关、酶组与过滤、Ctrl+F 搜索、导出）、
 * 环形/线性/序列/分屏视图、特征与酶切侧栏、选区统计状态栏。
 * 只消费一个 MapDocument；独立 /map 页 = 上传解析 + <MapViewer :doc>。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { useElementSize } from '@vueuse/core'
import type { EnzymeDetail, EnzymeSet, EnzymeSupplier, FeatureType, MapDocument, Strand } from '../types'
import { createMapStore, provideMapStore, type ViewMode } from '../store'
import { analyzeMap, exportGenbank, getEnzymeDetail, getEnzymeSets, getSuppliers } from '../api'
import { supplierShortName } from '../enzymeMeta'
import { rangeLength } from '../core/coords'
import { blockedSiteCount, enzymeStyle, GOLDEN_GATE_ENZYMES } from '../core/enzymes'
import { addFeature, deleteFeature, pushHistory, updateFeature, validateRange, type FeaturePatch } from '../core/edit'
import { FEATURE_TYPE_LABELS, featureColor } from '../core/palette'
import { formatLength } from '../core/ticks'
import { resolveHit } from '../hits'
import { downloadPng, downloadSvg, safeFilename } from '../export'
import { downloadBlob } from '@/utils/download'
import { formatApiError } from '@/api'
import { onTabKeydown } from '@/utils/tabKeys'
import CircularMap from './CircularMap.vue'
import LinearMap from './LinearMap.vue'
import SequenceMap from './SequenceMap.vue'
import Minimap from './Minimap.vue'
import MapTooltip from './MapTooltip.vue'
import DigestPanel from './DigestPanel.vue'
import { sitesIncomplete } from '../core/digest'

const props = withDefaults(defineProps<{
  doc: MapDocument
  initialView?: ViewMode
  /** 序列视图高度（px） */
  sequenceHeight?: number
  /** 允许调用 /api/map/analyze 切换酶组（离线/无权限时可关） */
  allowAnalyze?: boolean
  /** 允许编辑特征（重命名/改色/删除/从选区新增、重新分析）；结果页与载体详情页保持只读 */
  editable?: boolean
}>(), { sequenceHeight: 360, allowAnalyze: true, editable: false })

const emit = defineEmits<{ (e: 'doc-change', doc: MapDocument): void }>()

const docRef = ref(props.doc) as Ref<MapDocument>
/** 编辑撤销栈（editable） */
const history = ref<MapDocument[]>([])
/**
 * 文档代次：父组件换了另一份文档时 +1。异步分析（切换酶组 / 重新分析）发出时记下代次，
 * 返回时代次变了就整体丢弃，不覆盖新文档、不发 doc-change。
 */
let docGen = 0
watch(() => props.doc, (d) => {
  if (d === docRef.value) return // 父组件回传本查看器刚发出的编辑结果
  docGen++
  docRef.value = d
  history.value = []
  enzymeSet.value = d.meta.enzyme_set || 'common'
  analyzing.value = false
  reanalyzing.value = false
})
const store = provideMapStore(createMapStore(docRef))
const doc = computed(() => docRef.value)
const isCircular = computed(() => doc.value.topology === 'circular')

const root = ref<HTMLElement | null>(null)
const mainBox = ref<HTMLElement | null>(null)
const { width: mainW } = useElementSize(mainBox)
const view = ref<ViewMode>(props.initialView ?? 'split')
const showCircular = computed(() => isCircular.value && (view.value === 'circular' || view.value === 'split'))
const showLinear = computed(() => view.value === 'linear' || (view.value === 'split' && !isCircular.value))
const showSequence = computed(() => view.value === 'sequence' || view.value === 'split')
const circularMax = computed(() => (view.value === 'split' ? 640 : 860))
watch(isCircular, (c) => { if (!c && view.value === 'circular') view.value = 'linear' }, { immediate: true })

const VIEWS: { id: ViewMode; label: string }[] = [
  { id: 'circular', label: '环形' }, { id: 'linear', label: '线性' }, { id: 'sequence', label: '序列' }, { id: 'split', label: '分屏' },
]
const LAYERS = [
  { key: 'features', label: '特征' }, { key: 'enzymes', label: '酶切' }, { key: 'orfs', label: 'ORF' },
  { key: 'primers', label: '引物' }, { key: 'labels', label: '标签' }, { key: 'complement', label: '互补链' },
  { key: 'translations', label: '翻译' },
] as const

// ---------------- 酶组 / 过滤 ----------------
const FALLBACK_SETS: EnzymeSet[] = [
  { id: 'common', name: '常用酶', description: '', enzymes: [] },
  { id: 'unique6', name: '单切点 6+ 碱基酶', description: '', enzymes: [] },
  { id: 'commercial', name: '全部商品化酶', description: '', enzymes: [] },
  { id: 'golden_gate', name: 'Golden Gate', description: '', enzymes: GOLDEN_GATE_ENZYMES },
]
const enzymeSets = ref<EnzymeSet[]>(FALLBACK_SETS)
const enzymeSet = ref(doc.value.meta.enzyme_set || 'common')
const analyzing = ref(false)
const analyzeError = ref('')
watch(() => doc.value.meta.enzyme_set, (s) => { if (s) enzymeSet.value = s })

onMounted(async () => {
  if (!props.allowAnalyze) return
  try {
    const sets = await getEnzymeSets()
    if (Array.isArray(sets) && sets.length) enzymeSets.value = sets
  } catch { /* 保留内置列表 */ }
})

/** 请求发出后序列是否仍是同一条（编辑只改特征/引物，不改序列；换文档或序列变了就不能合并） */
function sameMolecule(a: MapDocument, b: MapDocument) {
  return a.sequence === b.sequence && a.topology === b.topology && a.length === b.length
}
/** 各类异步分析的最新请求号：只有最后一次请求的结果生效 */
let enzymeSetToken = 0
let reanalyzeToken = 0

async function changeEnzymeSet() {
  const id = enzymeSet.value
  if (id === 'unique6') store.enzymeFilter.maxCuts = 1
  if (id === doc.value.meta.enzyme_set) return
  const token = ++enzymeSetToken
  const gen = docGen
  const isCurrent = () => token === enzymeSetToken && gen === docGen
  analyzing.value = true
  analyzeError.value = ''
  const d = doc.value
  try {
    const res = await analyzeMap({
      sequence: d.sequence, topology: d.topology, name: d.name, features: d.features, annotate: false, enzyme_set: id,
    })
    const cur = docRef.value
    if (!isCurrent() || !sameMolecule(cur, d)) return // 已换文档：丢弃过期结果
    // 只合并分析字段，请求期间的特征编辑保留
    docRef.value = { ...cur, enzymes: res.enzymes, orfs: res.orfs ?? cur.orfs, meta: { ...cur.meta, enzyme_set: id } }
    store.enzymeFilter.hidden = []
    emit('doc-change', docRef.value)
  } catch (e) {
    if (!isCurrent()) return
    analyzeError.value = `切换酶组失败：${formatApiError(e, '请求失败')}`
    enzymeSet.value = d0Set()
  } finally {
    if (isCurrent()) analyzing.value = false
  }
}
function d0Set() { return doc.value.meta.enzyme_set || 'common' }

const cutFilter = computed({
  get: () => String(store.enzymeFilter.maxCuts ?? 'any'),
  set: (v: string) => { store.enzymeFilter.maxCuts = v === 'any' ? null : (Number(v) as 1 | 2) },
})
const goldenGateOnly = computed({
  get: () => !!store.enzymeFilter.only,
  set: (v: boolean) => { store.enzymeFilter.only = v ? GOLDEN_GATE_ENZYMES : null },
})

// ---------------- 搜索（Ctrl+F） ----------------
const searchInput = ref<HTMLInputElement | null>(null)
const searchText = ref('')
const mismatches = ref(0)
let pointerInside = false
function setInside(v: boolean) { pointerInside = v }
function runSearch() { store.runSearch(searchText.value, mismatches.value) }
function onSearchKey(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    const step = e.shiftKey ? -1 : 1
    if (store.search.mode === 'name' && store.search.query === searchText.value
        && store.search.featureHits.length) {
      store.focusFeatureHit(store.search.featureIndex + step)
    } else if (store.search.hits.length && store.search.query === searchText.value.replace(/\s+/g, '').toUpperCase()) {
      store.focusHit(store.search.index + step)
    } else {
      runSearch()
    }
  } else if (e.key === 'Escape') {
    searchText.value = ''
    store.clearSearch()
  }
}
watch(mismatches, () => { if (searchText.value) runSearch() })
function onGlobalKey(e: KeyboardEvent) {
  if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'f') return
  const active = typeof document !== 'undefined' ? document.activeElement : null
  if (!pointerInside && !(root.value && active && root.value.contains(active))) return
  e.preventDefault()
  searchInput.value?.focus()
  searchInput.value?.select()
}
onMounted(() => window.addEventListener('keydown', onGlobalKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onGlobalKey))

// ---------------- 导出 ----------------
const circularRef = ref<InstanceType<typeof CircularMap> | null>(null)
const linearRef = ref<InstanceType<typeof LinearMap> | null>(null)
const exportError = ref('')
function currentSvg(): SVGSVGElement | null {
  return (showCircular.value ? circularRef.value?.svgEl : null) ?? (showLinear.value ? linearRef.value?.svgEl : null) ?? null
}
const canExportImage = computed(() => showCircular.value || showLinear.value)
async function exportImage(kind: 'svg' | 'png') {
  exportError.value = ''
  const svg = currentSvg()
  if (!svg) return
  try {
    if (kind === 'svg') downloadSvg(svg, doc.value.name)
    else await downloadPng(svg, doc.value.name, 2)
  } catch (e) {
    exportError.value = `导出失败：${(e as Error).message}`
  }
}
async function exportGb() {
  exportError.value = ''
  try {
    const blob = await exportGenbank(doc.value)
    downloadBlob(blob, `${safeFilename(doc.value.name)}.gb`)
  } catch (e) {
    exportError.value = `导出 GenBank 失败：${formatApiError(e, '请求失败')}`
  }
}

// ---------------- 编辑（editable） ----------------
const editError = ref('')
const reannotate = ref(false)
const reanalyzing = ref(false)
const TYPE_OPTIONS = Object.entries(FEATURE_TYPE_LABELS) as [FeatureType, string][]
const STRAND_OPTIONS: { v: Strand; label: string }[] = [{ v: 1, label: '正链 →' }, { v: -1, label: '反链 ←' }, { v: 0, label: '无方向' }]

function apply(next: MapDocument) {
  if (next === docRef.value) return
  history.value = pushHistory(history.value, docRef.value)
  docRef.value = next
  editError.value = ''
  emit('doc-change', next)
}
function undo() {
  const prev = history.value[history.value.length - 1]
  if (!prev) return
  history.value = history.value.slice(0, -1)
  docRef.value = prev
  emit('doc-change', prev)
}
function patchSelected(patch: FeaturePatch) {
  const f = store.selectedFeature.value
  if (f) apply(updateFeature(docRef.value, f.id, patch))
}
function onRename(e: Event) { patchSelected({ name: (e.target as HTMLInputElement).value }) }
function onRecolor(e: Event) { patchSelected({ color: (e.target as HTMLInputElement).value }) }
function onRetype(e: Event) { patchSelected({ type: (e.target as HTMLSelectElement).value as FeatureType }) }
function onRestrand(e: Event) { patchSelected({ strand: Number((e.target as HTMLSelectElement).value) as Strand }) }
function removeSelected() {
  const f = store.selectedFeature.value
  if (!f) return
  apply(deleteFeature(docRef.value, f.id))
  store.select(null)
}

const draft = ref<{ name: string; type: FeatureType; strand: Strand }>({ name: '', type: 'misc_feature', strand: 1 })
const canAdd = computed(() => !!store.selection.value && !store.selectedFeatureId.value)
const addRangeError = computed(() => validateRange(doc.value, store.selection.value))
function addFromSelection() {
  const r = store.selection.value
  try {
    if (!r) throw new Error('请先在图谱上拖选一段区域')
    const next = addFeature(docRef.value, r, draft.value)
    apply(next)
    const created = next.features[next.features.length - 1]
    store.select({ start: created.start, end: created.end }, { featureId: created.id })
    draft.value = { ...draft.value, name: '' }
  } catch (e) {
    editError.value = (e as Error).message
  }
}

async function reanalyze() {
  const token = ++reanalyzeToken
  const gen = docGen
  const isCurrent = () => token === reanalyzeToken && gen === docGen
  reanalyzing.value = true
  editError.value = ''
  const d = docRef.value
  const annotate = reannotate.value
  try {
    const res = await analyzeMap({
      sequence: d.sequence, topology: d.topology, name: d.name, features: d.features, primers: d.primers,
      annotate, enzyme_set: d.meta.enzyme_set || 'common',
    })
    const cur = docRef.value
    if (!isCurrent() || !sameMolecule(cur, d)) return // 已换文档：丢弃过期结果
    if (cur === d) {
      // 请求期间没有编辑：按原逻辑整篇采用分析结果
      apply({ ...res, primers: res.primers?.length ? res.primers : d.primers, meta: { ...res.meta, source_format: d.meta.source_format } })
      return
    }
    // 请求期间有编辑：以当前文档为底，只合并分析字段（酶切、ORF、新识别的自动特征）
    const key = (f: MapDocument['features'][number]) => `${f.type}|${f.start}|${f.end}|${f.strand}|${f.name}`
    const known = new Set([...d.features, ...cur.features].map(key))
    const newAuto = annotate ? res.features.filter((f) => f.source === 'auto' && !known.has(key(f))) : []
    const usedIds = new Set(cur.features.map((f) => f.id))
    const added = newAuto.map((f) => {
      let id = f.id
      for (let i = 2; usedIds.has(id); i++) id = `${f.id}_${i}`
      usedIds.add(id)
      return id === f.id ? f : { ...f, id }
    })
    apply({
      ...cur,
      enzymes: res.enzymes,
      orfs: res.orfs ?? cur.orfs,
      features: added.length ? [...cur.features, ...added] : cur.features,
      meta: { ...cur.meta, warnings: res.meta?.warnings ?? cur.meta.warnings },
    })
  } catch (e) {
    if (!isCurrent()) return
    editError.value = `重新分析失败：${formatApiError(e, '请求失败')}`
  } finally {
    if (isCurrent()) reanalyzing.value = false
  }
}

// ---------------- 甲基化阻断 ----------------
const hasBlocked = computed(() => doc.value.enzymes.some((e) => blockedSiteCount(e) > 0))

// ---------------- 侧栏 ----------------
const sideTab = ref<'features' | 'enzymes' | 'digest'>('features')
const featureRows = computed(() => [...doc.value.features]
  .sort((a, b) => a.start - b.start || b.end - a.end)
  .map((f) => ({ f, color: featureColor(f), len: rangeLength(f, doc.value.length, isCircular.value), hidden: store.hiddenFeatures.value.includes(f.id) })))
const enzymeRows = computed(() => store.enzymeList.value.map((e) => ({
  e, style: enzymeStyle(e), hidden: store.enzymeFilter.hidden.includes(e.name), blocked: blockedSiteCount(e),
  sites: e.sites.slice(0, 4).map((s) => s.cut_top).join(', ') + (e.sites.length > 4 || sitesIncomplete(e) ? ' …' : ''),
  truncated: sitesIncomplete(e),
})))
function selectEnzyme(name: string) {
  resolveHit(store, `e:${name}#0`)?.select()
  if (props.allowAnalyze) void showEnzymeDetail(name)
}

// ---------------- 厂商过滤 / 酶详情（REBASE，按需从后端取） ----------------
const suppliers = ref<EnzymeSupplier[]>([])
const supplierCode = ref('')
const suppliersFailed = ref(false)
let suppliersRequested = false
async function ensureSuppliers() {
  if (suppliersRequested || !props.allowAnalyze) return
  suppliersRequested = true
  try {
    const rows = await getSuppliers()
    if (Array.isArray(rows)) suppliers.value = rows
    suppliersFailed.value = false
  } catch {
    suppliersRequested = false
    suppliersFailed.value = true
  }
}
// 酶切面板第一次打开时才取厂商表（静态数据，会话内缓存）
watch(sideTab, (t) => { if (t === 'enzymes') void ensureSuppliers() }, { immediate: true })
watch(supplierCode, (code) => {
  const row = suppliers.value.find((x) => x.code === code)
  store.enzymeFilter.supplier = row ? row.enzymes : null
})
const supplierEmptyHint = computed(() => !!supplierCode.value && !enzymeRows.value.length)

const detailName = ref<string | null>(null)
const detail = ref<EnzymeDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
async function showEnzymeDetail(name: string) {
  detailName.value = name
  detailError.value = ''
  detail.value = null
  detailLoading.value = true
  try {
    const d = await getEnzymeDetail(name)
    if (detailName.value === name) detail.value = d
  } catch (e) {
    if (detailName.value === name) detailError.value = `酶详情加载失败：${formatApiError(e, '请求失败')}`
  } finally {
    if (detailName.value === name) detailLoading.value = false
  }
}
function closeDetail() { detailName.value = null; detail.value = null; detailError.value = '' }
const detailEnds = computed(() => {
  const d = detail.value
  if (!d) return ''
  return d.ends === 'blunt' ? '平端' : `${d.ends} 突出${d.overhang ? ` ${d.overhang}` : ''}`
})

// ---------------- 状态栏 ----------------
const selectionText = computed(() => {
  const s = store.selection.value
  if (!s) return ''
  const st = store.stats.value
  const wrap = s.start > s.end ? '（跨原点）' : ''
  return `已选 ${s.start.toLocaleString()}..${s.end.toLocaleString()}${wrap} · ${formatLength(rangeLength(s, doc.value.length, isCircular.value))}`
    + (st ? ` · GC ${st.gc.toFixed(1)}% · Tm ${st.tm.toFixed(1)} °C` : '')
})
const unshownItems = computed(() => {
  const views = [showCircular.value && 'circular', showLinear.value && 'linear', showSequence.value && 'sequence'].filter(Boolean) as string[]
  const seen = new Map<string, { id: string; name: string; reason: string }>()
  for (const v of views) for (const it of store.unshown[v] ?? []) if (!seen.has(it.id)) seen.set(it.id, it)
  return [...seen.values()]
})
const showUnshown = ref(false)
/** 详情列表最多列这么多条（300 kb 随机序列可有上千项，全列会卡） */
const UNSHOWN_LIST_MAX = 200
const unshownList = computed(() => {
  const items = unshownItems.value.slice(0, UNSHOWN_LIST_MAX)
  // 同名特征（如 pET-28a 的两段 6xHis）补上坐标以便区分
  const counts = new Map<string, number>()
  for (const it of items) counts.set(it.name, (counts.get(it.name) ?? 0) + 1)
  const featById = new Map(doc.value.features.map((f) => [f.id, f]))
  return items.map((it) => {
    if ((counts.get(it.name) ?? 0) < 2) return it
    const f = featById.get(it.id.replace(/^f:/, ''))
    return f ? { ...it, name: `${it.name}（${f.start.toLocaleString()}..${f.end.toLocaleString()}）` } : it
  })
})

defineExpose({ store, view, undo })
</script>

<template>
  <div ref="root" class="mv" @pointerenter="setInside(true)" @pointerleave="setInside(false)">
    <div class="mv-toolbar" role="toolbar" aria-label="图谱工具栏">
      <div class="mv-group mv-views" role="group" aria-label="视图">
        <button v-for="v in VIEWS" :key="v.id" type="button" :class="['mv-btn', { active: view === v.id }]"
                :disabled="v.id === 'circular' && !isCircular" :aria-pressed="view === v.id" @click="view = v.id">{{ v.label }}</button>
      </div>
      <div class="mv-group mv-layers" role="group" aria-label="图层">
        <label v-for="l in LAYERS" :key="l.key" class="mv-chip">
          <input v-model="store.show[l.key]" type="checkbox" /> {{ l.label }}
        </label>
      </div>
      <div class="mv-group mv-enzymes" role="group" aria-label="酶切">
        <select v-model="enzymeSet" class="mv-select" title="酶组（由后端重算酶切位点）" :disabled="!allowAnalyze || analyzing" @change="changeEnzymeSet">
          <option v-for="s in enzymeSets" :key="s.id" :value="s.id">{{ s.name }}</option>
        </select>
        <select v-model="cutFilter" class="mv-select" title="按切点数过滤">
          <option value="any">任意切点数</option>
          <option value="1">仅单切点</option>
          <option value="2">≤ 2 个切点</option>
        </select>
        <label class="mv-chip" title="只显示 Golden Gate 常用 IIS 型酶（BsaI/BsmBI/BbsI/SapI/Esp3I/PaqCI）">
          <input v-model="goldenGateOnly" type="checkbox" /> Golden Gate
        </label>
        <label v-if="hasBlocked" class="mv-chip mv-blocked-toggle" title="被 dam/dcm 甲基化阻断的位点以灰色虚线显示">
          <input v-model="store.show.blocked" type="checkbox" /> 显示甲基化阻断位点
        </label>
        <span v-if="analyzing" class="mv-muted">计算中…</span>
      </div>
      <div class="mv-group mv-search" role="search">
        <input ref="searchInput" v-model="searchText" class="mv-input" type="search" placeholder="搜索序列或元件名称（Ctrl+F）"
               aria-label="搜索序列或元件名称" @keydown="onSearchKey" />
        <select v-model.number="mismatches" class="mv-select" title="允许错配数">
          <option v-for="k in [0, 1, 2, 3]" :key="k" :value="k">{{ k }} 错配</option>
        </select>
        <button type="button" class="mv-btn" title="搜索" @click="runSearch">搜索</button>
        <template v-if="store.search.hits.length">
          <span class="mv-muted mv-hitcount">{{ store.search.index + 1 }}/{{ store.search.hits.length }}{{ store.search.truncated ? '+' : '' }}</span>
          <button type="button" class="mv-btn" title="上一个" @click="store.focusHit(store.search.index - 1)">↑</button>
          <button type="button" class="mv-btn" title="下一个" @click="store.focusHit(store.search.index + 1)">↓</button>
        </template>
        <template v-else-if="store.search.featureHits.length">
          <span class="mv-muted mv-hitcount" :title="store.search.featureHits[store.search.featureIndex]?.name">
            {{ store.search.featureIndex + 1 }}/{{ store.search.featureHits.length }} 个元件
          </span>
          <button type="button" class="mv-btn" title="上一个元件" @click="store.focusFeatureHit(store.search.featureIndex - 1)">↑</button>
          <button type="button" class="mv-btn" title="下一个元件" @click="store.focusFeatureHit(store.search.featureIndex + 1)">↓</button>
        </template>
        <span v-else-if="store.search.error" class="mv-error">{{ store.search.error }}</span>
        <span v-else-if="store.search.query" class="mv-muted">无匹配</span>
      </div>
      <div v-if="editable" class="mv-group mv-edit-tools" role="group" aria-label="编辑">
        <button type="button" class="mv-btn" :disabled="!history.length" title="撤销上一步编辑" @click="undo">撤销</button>
        <label class="mv-chip" title="重新分析时丢弃并重算自动注释（已编辑过的特征保留）"><input v-model="reannotate" type="checkbox" /> 重新自动注释</label>
        <button type="button" class="mv-btn mv-reanalyze" :disabled="!allowAnalyze || reanalyzing" title="按当前特征重算酶切位点与 ORF" @click="reanalyze">
          {{ reanalyzing ? '分析中…' : '重新分析' }}
        </button>
      </div>
      <div class="mv-group mv-export" role="group" aria-label="导出">
        <button type="button" class="mv-btn" :disabled="!canExportImage" title="导出当前图谱为 SVG" @click="exportImage('svg')">SVG</button>
        <button type="button" class="mv-btn" :disabled="!canExportImage" title="导出当前图谱为 PNG（2 倍分辨率）" @click="exportImage('png')">PNG</button>
        <button type="button" class="mv-btn" title="导出 GenBank（含特征与颜色）" @click="exportGb">GenBank</button>
      </div>
    </div>
    <p v-if="analyzeError || exportError || editError" class="mv-error mv-banner">{{ analyzeError || exportError || editError }}</p>
    <p v-for="(w, i) in doc.meta.warnings" :key="i" class="mv-warning mv-banner">{{ w }}</p>

    <div class="mv-body">
      <div ref="mainBox" class="mv-main">
        <CircularMap v-if="showCircular" ref="circularRef" :max-size="Math.min(circularMax, Math.max(240, mainW || circularMax))" />
        <template v-if="showLinear">
          <LinearMap ref="linearRef" />
          <Minimap follow="linear" />
        </template>
        <template v-if="showSequence">
          <Minimap v-if="view === 'sequence'" follow="sequence" />
          <SequenceMap :height="sequenceHeight" />
        </template>
      </div>

      <aside class="mv-side">
        <div class="mv-tabs" role="tablist" @keydown="onTabKeydown">
          <button type="button" role="tab" :class="['mv-tab', { active: sideTab === 'features' }]" :aria-selected="sideTab === 'features'"
                  @click="sideTab = 'features'">特征 ({{ doc.features.length }})</button>
          <button type="button" role="tab" :class="['mv-tab', { active: sideTab === 'enzymes' }]" :aria-selected="sideTab === 'enzymes'"
                  @click="sideTab = 'enzymes'">酶切 ({{ store.enzymeList.value.length }})</button>
          <button type="button" role="tab" :class="['mv-tab', { active: sideTab === 'digest' }]" :aria-selected="sideTab === 'digest'"
                  @click="sideTab = 'digest'">消化</button>
        </div>
        <div v-if="editable && sideTab === 'features'" class="mv-editor">
          <template v-if="store.selectedFeature.value">
            <div class="mv-editor-title">编辑特征</div>
            <div class="mv-editor-row">
              <input :key="`n-${store.selectedFeature.value.id}`" class="mv-input mv-edit-name" :value="store.selectedFeature.value.name"
                     aria-label="特征名称" maxlength="80" @change="onRename" />
              <input :key="`c-${store.selectedFeature.value.id}`" class="mv-color" type="color" aria-label="特征颜色"
                     :value="featureColor(store.selectedFeature.value)" @change="onRecolor" />
            </div>
            <div class="mv-editor-row">
              <select class="mv-select" aria-label="特征类型" :value="store.selectedFeature.value.type" @change="onRetype">
                <option v-for="[t, label] in TYPE_OPTIONS" :key="t" :value="t">{{ label }}</option>
              </select>
              <select class="mv-select" aria-label="方向" :value="store.selectedFeature.value.strand" @change="onRestrand">
                <option v-for="o in STRAND_OPTIONS" :key="o.v" :value="o.v">{{ o.label }}</option>
              </select>
              <button v-if="store.selectedFeature.value.color" type="button" class="mv-btn" title="恢复按类型配色" @click="patchSelected({ color: '' })">默认色</button>
              <button type="button" class="mv-btn mv-danger mv-delete" @click="removeSelected">删除</button>
            </div>
          </template>
          <template v-else-if="canAdd">
            <div class="mv-editor-title">从选区新增特征 <small class="mv-muted">{{ store.selection.value!.start }}..{{ store.selection.value!.end }}</small></div>
            <div class="mv-editor-row">
              <input v-model="draft.name" class="mv-input mv-new-name" placeholder="名称" aria-label="新特征名称" maxlength="80" @keydown.enter="addFromSelection" />
            </div>
            <div class="mv-editor-row">
              <select v-model="draft.type" class="mv-select" aria-label="新特征类型">
                <option v-for="[t, label] in TYPE_OPTIONS" :key="t" :value="t">{{ label }}</option>
              </select>
              <select v-model.number="draft.strand" class="mv-select" aria-label="新特征方向">
                <option v-for="o in STRAND_OPTIONS" :key="o.v" :value="o.v">{{ o.label }}</option>
              </select>
              <button type="button" class="mv-btn mv-add" :disabled="!draft.name.trim() || !!addRangeError" @click="addFromSelection">添加</button>
            </div>
          </template>
          <p v-else class="mv-muted mv-editor-hint">点选特征可编辑；在图上拖选一段区域可新增特征</p>
        </div>
        <ul v-if="sideTab === 'features'" class="mv-list mv-feature-list">
          <li v-if="!featureRows.length" class="mv-muted mv-empty">没有特征注释</li>
          <li v-for="r in featureRows" :key="r.f.id"
              :class="['mv-row', { selected: store.selectedFeatureId.value === r.f.id, hidden: r.hidden }]"
              @click="store.selectFeature(r.f.id)" @mouseenter="store.hover.value = null">
            <i class="mv-swatch" :style="{ background: r.color }"></i>
            <span class="mv-name" :title="r.f.notes || r.f.name">{{ r.f.name }}<small v-if="r.f.source === 'auto'" class="mv-auto">自动</small></span>
            <span class="mv-meta">{{ FEATURE_TYPE_LABELS[r.f.type] ?? r.f.type }} · {{ r.f.start }}..{{ r.f.end }} · {{ r.len }} bp {{ r.f.strand === -1 ? '←' : r.f.strand === 1 ? '→' : '' }}</span>
            <button type="button" class="mv-eye" :title="r.hidden ? '显示' : '隐藏'" :aria-label="r.hidden ? `显示 ${r.f.name}` : `隐藏 ${r.f.name}`"
                    @click.stop="store.toggleFeatureHidden(r.f.id)">{{ r.hidden ? '○' : '●' }}</button>
          </li>
        </ul>
        <div v-else-if="sideTab === 'enzymes'" class="mv-enzyme-panel">
          <div class="mv-enz-filters">
            <input v-model="store.enzymeFilter.query" class="mv-input mv-enz-query" type="search" placeholder="按酶名或识别序列筛选" aria-label="筛选酶" />
            <select v-model="supplierCode" class="mv-select mv-supplier" aria-label="按厂商筛选"
                    :title="suppliersFailed ? '厂商列表加载失败' : '只显示该厂商可供的酶（REBASE 厂商数据）'" :disabled="!suppliers.length">
              <option value="">全部厂商</option>
              <option v-for="sp in suppliers" :key="sp.code" :value="sp.code">{{ supplierShortName(sp) }}（{{ sp.enzymes.length }}）</option>
            </select>
          </div>
          <ul class="mv-list mv-enzyme-list">
            <li v-if="!enzymeRows.length" class="mv-muted mv-empty">没有符合条件的酶<template v-if="supplierEmptyHint">（所选厂商在当前酶组中没有切点；可换「全部商品酶」酶组）</template></li>
            <li v-for="r in enzymeRows" :key="r.e.name" :class="['mv-row', { hidden: r.hidden }]" @click="selectEnzyme(r.e.name)">
              <span class="mv-name" :style="{ color: r.style.color, fontWeight: r.style.weight }">{{ r.e.name }}</span>
              <span class="mv-meta">{{ r.e.recognition }} · {{ r.e.cut_count }} 切点 · {{ r.sites }}<b v-if="r.blocked" class="mv-blocked-badge" :title="`${r.blocked} 个位点被 dam/dcm 甲基化阻断`"> · {{ r.blocked }} 阻断</b><b v-if="r.truncated" class="mv-truncated-badge" :title="`切点过多，只返回了 ${r.e.sites.length} 个位点明细（图上只画这些），不能用于消化模拟`"> · 明细不全</b></span>
              <button type="button" class="mv-eye" :title="r.hidden ? '显示' : '隐藏'" :aria-label="r.hidden ? `显示 ${r.e.name}` : `隐藏 ${r.e.name}`"
                      @click.stop="store.toggleEnzymeHidden(r.e.name)">{{ r.hidden ? '○' : '●' }}</button>
            </li>
          </ul>
          <div v-if="detailName" class="mv-enz-detail" aria-live="polite">
            <div class="mv-enz-detail-head">
              <b>{{ detailName }}</b>
              <button type="button" class="mv-eye" aria-label="关闭酶详情" title="关闭" @click="closeDetail">×</button>
            </div>
            <p v-if="detailLoading" class="mv-muted">加载中…</p>
            <p v-else-if="detailError" class="mv-error">{{ detailError }}</p>
            <template v-else-if="detail">
              <div class="mv-enz-detail-line">{{ detail.recognition }} · {{ detailEnds }}</div>
              <div v-if="detail.type_iis || detail.golden_gate || detail.methylation?.length || detail.modification_dependent" class="mv-badges">
                <span v-if="detail.type_iis" class="mv-badge">Type IIS</span>
                <span v-if="detail.golden_gate" class="mv-badge">可用于 Golden Gate</span>
                <span v-if="detail.methylation?.length" class="mv-badge mv-badge-warn">甲基化敏感：{{ detail.methylation.join('/') }}</span>
                <span v-if="detail.modification_dependent" class="mv-badge mv-badge-warn" title="只切含甲基化等修饰的 DNA，不进图谱酶组">修饰依赖型</span>
              </div>
              <dl class="mv-enz-dl">
                <dt>厂商</dt>
                <dd>{{ detail.suppliers.length ? detail.suppliers.map((x) => x.name).join('、') : '无商业来源记录' }}</dd>
                <template v-if="detail.isoschizomers.length"><dt>同切酶</dt><dd>{{ detail.isoschizomers.join('、') }}</dd></template>
                <template v-if="detail.neoschizomers.length"><dt>新切酶</dt><dd>{{ detail.neoschizomers.join('、') }}</dd></template>
              </dl>
              <p class="mv-muted mv-enz-source">数据来源：REBASE（经 Biopython Bio.Restriction）</p>
            </template>
          </div>
          <p class="mv-legend mv-muted"><b>加粗</b> = 单切点 · <span style="color:#c0392b">红</span> = 平端 · <span style="color:#1f4e9c">蓝</span> = 5′ 突出 · <span style="color:#7b3fa0">紫</span> = 3′ 突出<template v-if="hasBlocked"> · <span style="color:#9ca3af">灰虚线</span> = 甲基化阻断</template></p>
        </div>
        <DigestPanel v-if="sideTab === 'digest'" :store="store" />
      </aside>
    </div>

    <div class="mv-status" aria-live="polite">
      <span class="mv-name-len"><b>{{ doc.name }}</b> · {{ formatLength(doc.length) }} · {{ isCircular ? '环状' : '线性' }}</span>
      <span v-if="selectionText" class="mv-selection-text">{{ selectionText }}</span>
      <span v-else-if="store.caret.value !== null" class="mv-muted">光标 {{ store.caret.value }}</span>
      <span v-if="unshownItems.length" class="mv-unshown">
        <button type="button" class="mv-link" @click="showUnshown = !showUnshown">{{ unshownItems.length }} 项无法显示（详情{{ showUnshown ? '▴' : '▾' }}）</button>
      </span>
    </div>
    <ul v-if="showUnshown && unshownItems.length" class="mv-unshown-list">
      <li v-for="it in unshownList" :key="it.id"><b>{{ it.name }}</b> <span class="mv-muted">— {{ it.reason }}</span></li>
      <li v-if="unshownItems.length > unshownList.length" class="mv-muted mv-unshown-more">…另有 {{ unshownItems.length - unshownList.length }} 项</li>
    </ul>
    <MapTooltip :store="store" />
  </div>
</template>

<style scoped>
.mv { background: var(--bg-primary); border: 1px solid var(--border-color); border-radius: 10px; padding: 0.6rem; color: var(--text-primary); }
.mv-toolbar { display: flex; flex-wrap: wrap; gap: 0.4rem 0.9rem; align-items: center; margin-bottom: 0.5rem; font-size: 13px; }
.mv-group { display: flex; flex-wrap: wrap; gap: 0.3rem; align-items: center; }
.mv-btn {
  padding: 3px 9px; border: 1px solid var(--border-color); border-radius: 5px; background: var(--bg-primary);
  color: var(--text-primary); font-size: 13px; line-height: 1.4;
}
.mv-btn.active { background: var(--primary-color); border-color: var(--primary-color); color: #fff; }
.mv-btn:disabled { opacity: 0.45; cursor: default; }
.mv-chip { display: inline-flex; gap: 3px; align-items: center; padding: 2px 6px; border-radius: 12px; background: var(--bg-secondary); cursor: pointer; user-select: none; }
.mv-select, .mv-input { padding: 3px 6px; border: 1px solid var(--border-color); border-radius: 5px; font-size: 13px; background: var(--bg-primary); color: var(--text-primary); }
.mv-input { width: 170px; }
.mv-muted { color: var(--text-secondary); font-size: 12px; }
.mv-error { color: var(--error-color); font-size: 12px; }
.mv-warning { color: #92400e; background: #fffbeb; font-size: 12px; }
.mv-banner { margin: 0 0 0.4rem; padding: 4px 8px; border-radius: 5px; }
.mv-body { display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 0.75rem; align-items: start; }
.mv-main { min-width: 0; display: flex; flex-direction: column; gap: 0.5rem; }
.mv-side { border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; display: flex; flex-direction: column; max-height: 760px; }
.mv-tabs { display: flex; border-bottom: 1px solid var(--border-color); }
.mv-tab { flex: 1; padding: 6px; border: none; background: var(--bg-secondary); font-size: 13px; color: var(--text-secondary); }
.mv-tab.active { background: var(--bg-primary); color: var(--primary-color); font-weight: 600; }
.mv-list { list-style: none; overflow-y: auto; font-size: 12.5px; }
.mv-enzyme-panel { display: flex; flex-direction: column; min-height: 0; }
.mv-enz-filters { display: flex; gap: 4px; margin: 6px; }
.mv-enz-query { width: auto; flex: 1; min-width: 0; }
.mv-supplier { max-width: 120px; }
.mv-enz-detail { padding: 6px 8px; border-top: 1px solid var(--border-color); background: var(--bg-secondary); font-size: 12px; }
.mv-enz-detail-head { display: flex; justify-content: space-between; align-items: center; }
.mv-enz-detail-head .mv-eye { grid-row: auto; grid-column: auto; }
.mv-enz-detail-line { margin: 2px 0; font-family: Consolas, Menlo, 'Courier New', monospace; }
.mv-badges { display: flex; flex-wrap: wrap; gap: 4px; margin: 3px 0; }
.mv-badge { padding: 0 6px; border-radius: 10px; background: #eef2ff; color: var(--primary-color); font-size: 11px; }
.mv-badge-warn { background: #fffbeb; color: #92400e; }
.mv-enz-dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 2px 6px; margin: 4px 0 0; }
.mv-enz-dl dt { color: var(--text-secondary); }
.mv-enz-dl dd { margin: 0; word-break: break-word; }
.mv-enz-source { margin-top: 4px; font-size: 11px; }
.mv-row { display: grid; grid-template-columns: 12px minmax(0, 1fr) 22px; grid-template-rows: auto auto; column-gap: 6px; padding: 5px 8px; border-bottom: 1px solid var(--bg-secondary); cursor: pointer; }
.mv-enzyme-list .mv-row { grid-template-columns: minmax(0, 1fr) 22px; }
.mv-row:hover { background: var(--bg-secondary); }
.mv-row.selected { background: #eef2ff; }
.mv-row.hidden .mv-name, .mv-row.hidden .mv-meta { opacity: 0.45; }
.mv-swatch { width: 10px; height: 10px; border-radius: 2px; margin-top: 5px; grid-row: span 2; }
.mv-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.mv-meta { grid-column: 2; color: var(--text-secondary); font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mv-enzyme-list .mv-meta { grid-column: 1; }
.mv-eye { grid-row: 1 / span 2; grid-column: -2; border: none; background: none; color: var(--text-secondary); font-size: 12px; }
.mv-auto { margin-left: 4px; font-size: 10px; color: var(--secondary-color); }
.mv-empty { padding: 10px; }
.mv-editor { padding: 6px 8px; border-bottom: 1px solid var(--border-color); background: var(--bg-secondary); font-size: 12.5px; }
.mv-editor-title { font-weight: 600; margin-bottom: 4px; }
.mv-editor-row { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 4px; align-items: center; }
.mv-editor-row .mv-input { flex: 1; min-width: 0; width: auto; }
.mv-editor-hint { padding: 2px 0; }
.mv-color { width: 32px; height: 26px; padding: 0 2px; border: 1px solid var(--border-color); border-radius: 5px; background: var(--bg-primary); }
.mv-danger { color: var(--error-color); border-color: var(--error-color); }
.mv-blocked-badge { color: #6b7280; font-weight: 500; }
.mv-truncated-badge { color: #92400e; font-weight: 500; }
.mv-legend { padding: 6px 8px; border-top: 1px solid var(--border-color); }
.mv-status { display: flex; flex-wrap: wrap; gap: 0.4rem 1rem; align-items: center; margin-top: 0.5rem; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.mv-selection-text { color: var(--primary-color); }
.mv-link { border: none; background: none; color: var(--warning-color); text-decoration: underline; font-size: 12.5px; padding: 0; }
.mv-unshown-list { margin: 0.3rem 0 0 1rem; font-size: 12px; max-height: 160px; overflow-y: auto; }
@media (max-width: 900px) {
  .mv-body { grid-template-columns: minmax(0, 1fr); }
  .mv-side { max-height: 360px; }
  .mv-input { width: 130px; }
}
</style>
