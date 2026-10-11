<script setup lang="ts">
/** 列表用缩略图：只有主链与特征色块，无标签、无交互 */
import { computed } from 'vue'
import type { MapDocument } from '../types'
import { arcBandPath, splitRange, unroll, xToAngle } from '../core/coords'
import { featureColor } from '../core/palette'

const props = withDefaults(defineProps<{
  doc: Pick<MapDocument, 'length' | 'topology' | 'features' | 'name'>
  size?: number
}>(), { size: 96 })

const L = computed(() => Math.max(1, props.doc.length))
const circular = computed(() => props.doc.topology === 'circular')
const c = computed(() => props.size / 2)
const R = computed(() => props.size / 2 - 6)

const arcs = computed(() => props.doc.features.map((f, i) => {
  const u = unroll(f, L.value)
  const a1 = xToAngle(u.from, L.value)
  const a2 = xToAngle(Math.max(u.to, u.from + L.value / 300), L.value)
  return { k: `${f.id}-${i}`, d: arcBandPath(c.value, c.value, R.value - 7, R.value - 1, a1, a2), color: featureColor(f) }
}))
const W = computed(() => props.size * 2)
const bars = computed(() => props.doc.features.flatMap((f) => splitRange(f, L.value).map((s, i) => ({
  k: `${f.id}-${i}`, x: 4 + ((s.start - 1) / L.value) * (W.value - 8), w: Math.max(1.5, ((s.end - s.start + 1) / L.value) * (W.value - 8)), color: featureColor(f),
}))))
</script>

<template>
  <svg v-if="circular" class="map-thumb" :width="size" :height="size" :viewBox="`0 0 ${size} ${size}`" role="img"
       :aria-label="`${doc.name} 缩略图`" xmlns="http://www.w3.org/2000/svg">
    <circle :cx="c" :cy="c" :r="R" fill="none" stroke="#9ca3af" stroke-width="1.2" />
    <path v-for="a in arcs" :key="a.k" :d="a.d" :fill="a.color" />
  </svg>
  <svg v-else class="map-thumb" :width="W" :height="24" :viewBox="`0 0 ${W} 24`" role="img" :aria-label="`${doc.name} 缩略图`" xmlns="http://www.w3.org/2000/svg">
    <rect x="4" y="11" :width="W - 8" height="2" fill="#9ca3af" />
    <rect v-for="b in bars" :key="b.k" :x="b.x" y="6" :width="b.w" height="12" :fill="b.color" rx="1" />
  </svg>
</template>
