<script setup lang="ts">
/**
 * 图谱查看器：工具栏（视图切换、图层开关、酶组与过滤、Ctrl+F 搜索、导出）、
 * 环形/线性/序列/分屏视图、特征与酶切侧栏、选区统计状态栏。
 * 只消费一个 MapDocument；独立 /map 页 = 上传解析 + <MapViewer :doc>。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { useElementSize } from '@vueuse/core'
import type { EnzymeSet, MapDocument } from '../types'
import { createMapStore, provideMapStore, type ViewMode } from '../store'
import { analyzeMap, exportGenbank, getEnzymeSets } from '../api'
import { rangeLength } from '../core/coords'
import { blockedSiteCount, enzymeStyle, GOLDEN_GATE_ENZYMES } from '../core/enzymes'
import { FEATURE_TYPE_LABELS, featureColor } from '../core/palette'
import { formatLength } from '../core/ticks'
import { resolveHit } from '../hits'
import { downloadPng, downloadSvg, safeFilename } from '../export'
import { downloadBlob } from '@/utils/download'
import { formatApiError } from '@/api'
import CircularMap from './CircularMap.vue'
import LinearMap from './LinearMap.vue'
import SequenceMap from './SequenceMap.vue'
import Minimap from './Minimap.vue'
import MapTooltip from './MapTooltip.vue'

const props = withDefaults(defineProps<{
  doc: MapDocument
  initialView?: ViewMode
  /** 序列视图高度（px） */
  sequenceHeight?: number
  /** 允许调用 /api/map/analyze 切换酶组（离线/无权限时可关） */
  allowAnalyze?: boolean
}>(), { sequenceHeight: 360, allowAnalyze: true })

const emit = defineEmits<{ (e: 'doc-change', doc: MapDocument): void }>()

const docRef = ref(props.doc) as Ref<MapDocument>
watch(() => props.doc, (d) => { docRef.value = d })
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

async function changeEnzymeSet() {
  const id = enzymeSet.value
  if (id === 'unique6') store.enzymeFilter.maxCuts = 1
  if (id === doc.value.meta.enzyme_set) return
  analyzing.value = true
  analyzeError.value = ''
  try {
    const d = doc.value
    const res = await analyzeMap({
      sequence: d.sequence, topology: d.topology, name: d.name, features: d.features, annotate: false, enzyme_set: id,
    })
    docRef.value = { ...d, enzymes: res.enzymes, orfs: res.orfs ?? d.orfs, meta: { ...d.meta, enzyme_set: id } }
    store.enzymeFilter.hidden = []
    emit('doc-change', docRef.value)
  } catch (e) {
    analyzeError.value = `切换酶组失败：${formatApiError(e, '请求失败')}`
    enzymeSet.value = d0Set()
  } finally {
    analyzing.value = false
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
    if (store.search.hits.length && store.search.query === searchText.value.replace(/\s+/g, '').toUpperCase()) {
      store.focusHit(store.search.index + (e.shiftKey ? -1 : 1))
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

// ---------------- 甲基化阻断 ----------------
const hasBlocked = computed(() => doc.value.enzymes.some((e) => blockedSiteCount(e) > 0))

// ---------------- 侧栏 ----------------
const sideTab = ref<'features' | 'enzymes'>('features')
const featureRows = computed(() => [...doc.value.features]
  .sort((a, b) => a.start - b.start || b.end - a.end)
  .map((f) => ({ f, color: featureColor(f), len: rangeLength(f, doc.value.length, isCircular.value), hidden: store.hiddenFeatures.value.includes(f.id) })))
const enzymeRows = computed(() => store.enzymeList.value.map((e) => ({
  e, style: enzymeStyle(e), hidden: store.enzymeFilter.hidden.includes(e.name), blocked: blockedSiteCount(e),
  sites: e.sites.slice(0, 4).map((s) => s.cut_top).join(', ') + (e.sites.length > 4 ? ' …' : ''),
})))
function selectEnzyme(name: string) { resolveHit(store, `e:${name}#0`)?.select() }

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

defineExpose({ store, view })
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
        <input ref="searchInput" v-model="searchText" class="mv-input" type="search" placeholder="搜索序列（Ctrl+F）"
               aria-label="搜索序列" @keydown="onSearchKey" />
        <select v-model.number="mismatches" class="mv-select" title="允许错配数">
          <option v-for="k in [0, 1, 2, 3]" :key="k" :value="k">{{ k }} 错配</option>
        </select>
        <button type="button" class="mv-btn" title="搜索" @click="runSearch">搜索</button>
        <template v-if="store.search.hits.length">
          <span class="mv-muted mv-hitcount">{{ store.search.index + 1 }}/{{ store.search.hits.length }}{{ store.search.truncated ? '+' : '' }}</span>
          <button type="button" class="mv-btn" title="上一个" @click="store.focusHit(store.search.index - 1)">↑</button>
          <button type="button" class="mv-btn" title="下一个" @click="store.focusHit(store.search.index + 1)">↓</button>
        </template>
        <span v-else-if="store.search.error" class="mv-error">{{ store.search.error }}</span>
        <span v-else-if="store.search.query" class="mv-muted">无匹配</span>
      </div>
      <div class="mv-group mv-export" role="group" aria-label="导出">
        <button type="button" class="mv-btn" :disabled="!canExportImage" title="导出当前图谱为 SVG" @click="exportImage('svg')">SVG</button>
        <button type="button" class="mv-btn" :disabled="!canExportImage" title="导出当前图谱为 PNG（2 倍分辨率）" @click="exportImage('png')">PNG</button>
        <button type="button" class="mv-btn" title="导出 GenBank（含特征与颜色）" @click="exportGb">GenBank</button>
      </div>
    </div>
    <p v-if="analyzeError || exportError" class="mv-error mv-banner">{{ analyzeError || exportError }}</p>
    <p v-for="(w, i) in doc.meta.warnings" :key="i" class="mv-warning mv-banner">{{ w }}</p>

    <div class="mv-body">
      <div ref="mainBox" class="mv-main">
        <CircularMap v-if="showCircular" ref="circularRef" :max-size="Math.min(circularMax, Math.max(300, mainW || circularMax))" />
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
        <div class="mv-tabs" role="tablist">
          <button type="button" role="tab" :class="['mv-tab', { active: sideTab === 'features' }]" :aria-selected="sideTab === 'features'"
                  @click="sideTab = 'features'">特征 ({{ doc.features.length }})</button>
          <button type="button" role="tab" :class="['mv-tab', { active: sideTab === 'enzymes' }]" :aria-selected="sideTab === 'enzymes'"
                  @click="sideTab = 'enzymes'">酶切 ({{ store.enzymeList.value.length }})</button>
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
        <div v-else class="mv-enzyme-panel">
          <input v-model="store.enzymeFilter.query" class="mv-input mv-enz-query" type="search" placeholder="按酶名或识别序列筛选" aria-label="筛选酶" />
          <ul class="mv-list mv-enzyme-list">
            <li v-if="!enzymeRows.length" class="mv-muted mv-empty">没有符合条件的酶</li>
            <li v-for="r in enzymeRows" :key="r.e.name" :class="['mv-row', { hidden: r.hidden }]" @click="selectEnzyme(r.e.name)">
              <span class="mv-name" :style="{ color: r.style.color, fontWeight: r.style.weight }">{{ r.e.name }}</span>
              <span class="mv-meta">{{ r.e.recognition }} · {{ r.e.cut_count }} 切点 · {{ r.sites }}<b v-if="r.blocked" class="mv-blocked-badge" :title="`${r.blocked} 个位点被 dam/dcm 甲基化阻断`"> · {{ r.blocked }} 阻断</b></span>
              <button type="button" class="mv-eye" :title="r.hidden ? '显示' : '隐藏'" :aria-label="r.hidden ? `显示 ${r.e.name}` : `隐藏 ${r.e.name}`"
                      @click.stop="store.toggleEnzymeHidden(r.e.name)">{{ r.hidden ? '○' : '●' }}</button>
            </li>
          </ul>
          <p class="mv-legend mv-muted"><b>加粗</b> = 单切点 · <span style="color:#c0392b">红</span> = 平端 · <span style="color:#1f4e9c">蓝</span> = 5′ 突出 · <span style="color:#7b3fa0">紫</span> = 3′ 突出<template v-if="hasBlocked"> · <span style="color:#9ca3af">灰虚线</span> = 甲基化阻断</template></p>
        </div>
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
      <li v-for="it in unshownItems" :key="it.id"><b>{{ it.name }}</b> <span class="mv-muted">— {{ it.reason }}</span></li>
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
.mv-enz-query { width: auto; margin: 6px; }
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
.mv-blocked-badge { color: #6b7280; font-weight: 500; }
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
