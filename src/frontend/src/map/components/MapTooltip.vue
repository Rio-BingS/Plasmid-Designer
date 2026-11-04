<script setup lang="ts">
/**
 * 悬停提示框（模块 C）：读取共享 hover；fixed 定位，贴近指针并避开视口边缘。
 *
 * 视觉：标题 13px/600；首行「类型 · 链向」作副标题；序列行等宽字体并加分隔线；
 * 宽 160~300px 自适应，行距 1.5，浅底深字对比度 ≥ 7:1。
 */
import { computed } from 'vue'
import type { MapStore } from '../store'

const props = defineProps<{ store: MapStore }>()

const MAX_W = 300

const style = computed(() => {
  const h = props.store.hover.value
  if (!h) return {}
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1200
  const vh = typeof window !== 'undefined' ? window.innerHeight : 800
  const left = h.x + 14 + MAX_W > vw ? Math.max(4, h.x - MAX_W - 14) : h.x + 14
  // 估算高度（标题 + 每行 ~19 px，长行按 2 行算），贴近底边时翻到指针上方
  const estH = 30 + h.lines.reduce((n, l) => n + (l.length > 40 ? 2 : 1), 0) * 19
  const top = h.y + 14 + estH > vh ? Math.max(4, h.y - 10 - estH) : h.y + 14
  return { left: `${left}px`, top: `${top}px`, maxWidth: `${MAX_W}px` }
})

/** 首行是「类型 · 链向」副标题，其余是正文 */
const subtitle = computed(() => props.store.hover.value?.lines[0] ?? '')
const bodyLines = computed(() => props.store.hover.value?.lines.slice(1) ?? [])
/** 纯碱基行（小写=突出区）按等宽序列渲染 */
const isSeq = (s: string) => /^[ACGTacgt\s]{12,}$/.test(s)
</script>

<template>
  <div v-if="store.hover.value" class="map-tooltip" :style="style" role="tooltip">
    <strong class="map-tooltip-title">{{ store.hover.value.title }}</strong>
    <div v-if="subtitle" class="map-tooltip-sub">{{ subtitle }}</div>
    <div v-for="(l, i) in bodyLines" :key="i" :class="['map-tooltip-line', { 'is-seq': isSeq(l) }]">{{ l }}</div>
  </div>
</template>

<style scoped>
.map-tooltip {
  position: fixed; z-index: 50; min-width: 160px; pointer-events: none;
  background: var(--bg-primary, #fff); color: var(--text-primary, #111827);
  border: 1px solid var(--border-color, #e5e7eb); border-radius: 8px;
  box-shadow: 0 6px 20px rgba(15, 23, 42, 0.14), 0 1px 3px rgba(15, 23, 42, 0.08);
  padding: 8px 10px; font-size: 12px; line-height: 1.5;
}
.map-tooltip-title { display: block; font-size: 13px; font-weight: 600; letter-spacing: -0.01em; margin-bottom: 1px; }
.map-tooltip-sub { color: var(--text-secondary, #6b7280); font-size: 11.5px; margin-bottom: 3px; }
.map-tooltip-line { color: var(--text-secondary, #4b5563); word-break: break-all; }
.map-tooltip-line.is-seq {
  margin-top: 3px; padding-top: 3px; border-top: 1px dashed var(--border-color, #e5e7eb);
  font-family: Consolas, Menlo, 'Courier New', monospace; font-size: 11px;
  color: var(--text-primary, #374151); letter-spacing: 0.02em;
}
</style>
