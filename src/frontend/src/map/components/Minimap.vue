<script setup lang="ts">
/**
 * 缩略导航条：全长概览（特征色块 + 选区）+ 当前视口框。
 * 点击/拖拽跳转：线性视图移动视口，序列视图滚动到对应行（经共享 navigate）。
 */
import { computed, ref } from 'vue'
import { useElementSize } from '@vueuse/core'
import type { MapDocument } from '../types'
import { useMapStore } from '../store'
import { splitRange } from '../core/coords'
import { featureColor } from '../core/palette'

const props = withDefaults(defineProps<{
  doc?: MapDocument
  /** 跟随哪个视图的可见区 */
  follow?: 'linear' | 'sequence'
}>(), { follow: 'linear' })

const { store } = useMapStore(() => props.doc)
const box = ref<HTMLElement | null>(null)
const { width } = useElementSize(box)
const W = computed(() => Math.max(200, Math.round(width.value || 800)))
const H = 26
const L = computed(() => store.L.value)
const x = (b: number) => (b / L.value) * W.value

const blocks = computed(() => store.features.value.flatMap((f) => splitRange(f, L.value).map((s, i) => ({
  k: `${f.id}-${i}`, x: x(s.start - 1), w: Math.max(1, x(s.end) - x(s.start - 1)), color: featureColor(f),
}))))
const sel = computed(() => (store.selection.value ? splitRange(store.selection.value, L.value).map((s, i) => ({ k: i, x: x(s.start - 1), w: Math.max(1.5, x(s.end) - x(s.start - 1)) })) : []))
const view = computed(() => {
  if (props.follow === 'sequence') {
    const v = store.seqVisible.value
    return v ? { x: x(v.start - 1), w: Math.max(3, x(v.end) - x(v.start - 1)) } : null
  }
  const v = store.viewport.value
  if (v.to - v.from >= L.value - 0.5) return null
  return { x: x(v.from), w: Math.max(3, x(v.to) - x(v.from)) }
})

let dragging = false
function go(e: PointerEvent) {
  const rect = box.value?.getBoundingClientRect()
  if (!rect || !rect.width) return
  const pos = Math.max(1, Math.min(L.value, Math.round(((e.clientX - rect.left) / rect.width) * L.value)))
  if (props.follow === 'linear') {
    const v = store.viewport.value
    const span = v.to - v.from
    const from = Math.max(0, Math.min(L.value - span, pos - span / 2))
    store.viewport.value = { from, to: from + span }
  } else {
    store.navigateTo(pos)
  }
}
function down(e: PointerEvent) { dragging = true; go(e); (e.currentTarget as Element | null)?.setPointerCapture?.(e.pointerId) }
function move(e: PointerEvent) { if (dragging) go(e) }
function up() { dragging = false }
</script>

<template>
  <div ref="box" class="minimap" title="缩略导航：点击或拖动跳转" @pointerdown="down" @pointermove="move" @pointerup="up" @pointercancel="up" @lostpointercapture="up">
    <svg :width="W" :height="H" :viewBox="`0 0 ${W} ${H}`" xmlns="http://www.w3.org/2000/svg" class="minimap-svg">
      <rect x="0" :y="H / 2 - 1" :width="W" height="2" fill="#9ca3af" />
      <rect v-for="b in blocks" :key="b.k" :x="b.x" y="6" :width="b.w" :height="H - 12" :fill="b.color" fill-opacity="0.85" />
      <rect v-for="s in sel" :key="'s' + s.k" :x="s.x" y="1" :width="s.w" :height="H - 2" fill="#4f46e5" fill-opacity="0.25" />
      <rect v-if="view" :x="view.x" y="0.5" :width="view.w" :height="H - 1" fill="none" stroke="#1d4ed8" stroke-width="1.5" rx="2" class="minimap-view" />
    </svg>
  </div>
</template>

<style scoped>
.minimap { width: 100%; cursor: pointer; touch-action: none; user-select: none; }
.minimap-svg { display: block; width: 100%; height: 26px; }
</style>
