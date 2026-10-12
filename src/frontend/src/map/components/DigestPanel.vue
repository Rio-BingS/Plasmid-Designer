<script setup lang="ts">
/**
 * 酶切消化面板（Benchling "Digest" 的简化版）：从当前可见酶列表选 1–3 个酶，
 * 列出片段（长度、坐标、两端酶，环状感知）并画一条虚拟凝胶泳道 + 1 kb ladder。
 * 计算全部在 core/digest.ts（纯函数）。
 */
import { computed, ref, watch } from 'vue'
import type { MapStore } from '../store'
import { blockedKinds, DEFAULT_GEL, digest, formatBp, gelBands, gelY, LADDER_1KB, sitesIncomplete, sortBySize, type DigestFragment } from '../core/digest'

const props = defineProps<{ store: MapStore }>()
const MAX = 3

const query = ref('')
const chosen = ref<string[]>([])
const skipBlocked = ref(true)

const doc = computed(() => props.store.doc.value)
const circular = computed(() => doc.value.topology === 'circular')
const candidates = computed(() => {
  const hidden = new Set(props.store.enzymeFilter.hidden)
  const q = query.value.trim().toLowerCase()
  return props.store.enzymeList.value.filter((e) => !hidden.has(e.name) && (!q || e.name.toLowerCase().includes(q)))
})
// 位点明细被截断的酶：少算切口会得出错误片段，不参与消化
const TRUNCATED_HINT = '切点过多，后端只返回了部分位点明细，无法模拟消化'
const truncatedNames = computed(() => new Set(doc.value.enzymes.filter(sitesIncomplete).map((e) => e.name)))
const hasTruncatedCandidate = computed(() => candidates.value.some((e) => truncatedNames.value.has(e.name)))
// 文档变化（换酶组/重新分析）后去掉已不存在或明细被截断的酶
watch(() => doc.value.enzymes, (list) => {
  const names = new Set(list.filter((e) => !sitesIncomplete(e)).map((e) => e.name))
  chosen.value = chosen.value.filter((n) => names.has(n))
})

function toggle(name: string) {
  const i = chosen.value.indexOf(name)
  if (i >= 0) chosen.value = chosen.value.filter((n) => n !== name)
  else if (chosen.value.length < MAX && !truncatedNames.value.has(name)) chosen.value = [...chosen.value, name]
}
function pickDisabled(name: string): boolean {
  if (chosen.value.includes(name)) return false
  return chosen.value.length >= MAX || truncatedNames.value.has(name)
}

const selectedEnzymes = computed(() => chosen.value
  .map((n) => doc.value.enzymes.find((e) => e.name === n))
  .filter((e): e is NonNullable<typeof e> => !!e && !sitesIncomplete(e)))
const blocked = computed(() => {
  const s = new Set<string>()
  for (const e of selectedEnzymes.value) for (const k of blockedKinds(e)) s.add(k)
  return [...s]
})
const result = computed(() => selectedEnzymes.value.length
  ? digest(doc.value.length, circular.value, selectedEnzymes.value, { skipBlocked: skipBlocked.value && blocked.value.length > 0 })
  : null)
const fragments = computed(() => (result.value ? sortBySize(result.value.fragments) : []))

const GEL_H = DEFAULT_GEL.bottom + 16
const ladder = LADDER_1KB.map((bp) => ({ bp, y: gelY(bp), label: formatBp(bp), bold: bp === 3000 || bp === 1000 }))
const bands = computed(() => gelBands(fragments.value.map((f) => f.length)))

function ends(f: DigestFragment): string {
  const l = f.left ? f.left.join('+') : '末端'
  const r = f.right ? f.right.join('+') : '末端'
  return `${l} – ${r}`
}
function selectFragment(f: DigestFragment) {
  if (result.value?.uncut) return
  props.store.select({ start: f.start, end: f.end }, { navigate: true })
}
</script>

<template>
  <div class="dg">
    <div class="dg-pick">
      <input v-model="query" class="dg-input" type="search" placeholder="筛选酶名" aria-label="筛选消化用酶" />
      <p class="dg-hint">从可见酶中选 1–{{ MAX }} 个（已选 {{ chosen.length }}）</p>
      <ul class="dg-enz-list">
        <li v-if="!candidates.length" class="dg-muted">没有可选的酶（检查酶切图层与过滤条件）</li>
        <li v-for="e in candidates" :key="e.name">
          <label :class="{ disabled: pickDisabled(e.name) }" :title="truncatedNames.has(e.name) ? TRUNCATED_HINT : undefined">
            <input type="checkbox" :checked="chosen.includes(e.name)" :disabled="pickDisabled(e.name)"
                   :data-enzyme="e.name" @change="toggle(e.name)" />
            {{ e.name }} <small class="dg-muted">{{ e.cut_count }} 切点<template v-if="truncatedNames.has(e.name)">（明细不全）</template></small>
          </label>
        </li>
      </ul>
      <p v-if="hasTruncatedCandidate" class="dg-truncated" role="note">标“明细不全”的酶{{ TRUNCATED_HINT }}；可在酶切列表查看其完整切点数。</p>
      <label v-if="blocked.length" class="dg-blocked">
        <input v-model="skipBlocked" type="checkbox" /> 跳过被 {{ blocked.join('/') }} 甲基化阻断的位点
      </label>
    </div>

    <div v-if="result" class="dg-result">
      <p class="dg-summary">
        {{ selectedEnzymes.map((e) => e.name).join(' + ') }}：
        <template v-if="result.uncut">未切开（环状，{{ formatBp(doc.length) }}）</template>
        <template v-else>{{ result.cuts.length }} 个切口 → {{ fragments.length }} 个片段</template>
        <span v-if="result.skippedBlocked" class="dg-muted">（已跳过 {{ result.skippedBlocked }} 个阻断位点）</span>
      </p>
      <div class="dg-body">
        <svg class="dg-gel" width="132" :height="GEL_H" :viewBox="`0 0 132 ${GEL_H}`" role="img" aria-label="虚拟凝胶" xmlns="http://www.w3.org/2000/svg">
          <rect x="44" y="4" width="84" :height="GEL_H - 6" rx="3" fill="#1f2430" />
          <rect x="52" y="6" width="28" height="5" fill="#3a4152" />
          <rect x="92" y="6" width="28" height="5" fill="#3a4152" />
          <g class="dg-ladder">
            <template v-for="b in ladder" :key="b.bp">
              <rect x="52" :y="b.y - 1" width="28" :height="b.bold ? 2.6 : 1.8" fill="#e8ecf5" :fill-opacity="b.bold ? 0.95 : 0.7" />
              <text x="40" :y="b.y + 3" font-size="8.5" text-anchor="end" fill="#6b7280">{{ b.label }}</text>
            </template>
          </g>
          <g class="dg-lane">
            <rect v-for="(b, i) in bands" :key="i" x="92" :y="b.y - 1.2" width="28" height="2.6" rx="0.6"
                  fill="#fff7d6" :fill-opacity="b.intensity" class="dg-band">
              <title>{{ formatBp(b.bp) }}{{ b.count > 1 ? `（${b.count} 条共迁移）` : '' }}{{ b.outOfRange ? '（超出分辨范围）' : '' }}</title>
            </rect>
          </g>
        </svg>
        <ol class="dg-frags">
          <li v-for="(f, i) in fragments" :key="`${f.start}-${f.end}-${i}`" class="dg-frag" @click="selectFragment(f)">
            <b>{{ i + 1 }}. {{ f.length.toLocaleString() }} bp</b>
            <span class="dg-muted">{{ result.uncut ? '环状' : `${f.start.toLocaleString()}..${f.end.toLocaleString()}${f.wraps ? '（跨原点）' : ''}` }}</span>
            <span v-if="!result.uncut" class="dg-muted dg-ends">{{ ends(f) }}</span>
          </li>
        </ol>
      </div>
      <p class="dg-muted dg-note">凝胶为对数迁移近似，仅供参考{{ result.uncut ? '；未切开的环状质粒实际迁移与线性 DNA 不同' : '' }}。</p>
    </div>
    <p v-else class="dg-muted dg-empty">勾选酶后显示片段与凝胶</p>
  </div>
</template>

<style scoped>
.dg { display: flex; flex-direction: column; min-height: 0; overflow-y: auto; font-size: 12.5px; }
.dg-pick { padding: 6px 8px; border-bottom: 1px solid var(--border-color); }
.dg-input { width: 100%; padding: 3px 6px; border: 1px solid var(--border-color); border-radius: 5px; font-size: 13px; background: var(--bg-primary); color: var(--text-primary); }
.dg-hint { margin: 4px 0; color: var(--text-secondary); font-size: 12px; }
.dg-enz-list { list-style: none; max-height: 150px; overflow-y: auto; display: grid; grid-template-columns: 1fr 1fr; gap: 1px 6px; }
.dg-enz-list label { display: flex; gap: 4px; align-items: center; cursor: pointer; white-space: nowrap; overflow: hidden; }
.dg-enz-list label.disabled { opacity: 0.45; cursor: default; }
.dg-truncated { margin-top: 4px; color: #92400e; font-size: 12px; }
.dg-blocked { display: flex; gap: 4px; align-items: center; margin-top: 4px; color: #92400e; font-size: 12px; }
.dg-result { padding: 6px 8px; }
.dg-summary { margin-bottom: 6px; }
.dg-body { display: flex; gap: 8px; align-items: flex-start; }
.dg-gel { flex: none; }
.dg-frags { list-style: none; flex: 1; min-width: 0; max-height: 300px; overflow-y: auto; }
.dg-frag { padding: 3px 4px; border-bottom: 1px solid var(--bg-secondary); cursor: pointer; display: flex; flex-direction: column; }
.dg-frag:hover { background: var(--bg-secondary); }
.dg-ends { font-size: 11px; }
.dg-muted { color: var(--text-secondary); font-size: 11.5px; }
.dg-note, .dg-empty { padding: 6px 8px; }
</style>
