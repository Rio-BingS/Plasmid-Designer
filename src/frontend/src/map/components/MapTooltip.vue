<script setup lang="ts">
/** 悬停提示框（读取共享 hover；fixed 定位，贴近指针并避开视口边缘） */
import { computed } from 'vue'
import type { MapStore } from '../store'

const props = defineProps<{ store: MapStore }>()

const style = computed(() => {
  const h = props.store.hover.value
  if (!h) return {}
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1200
  const left = h.x + 14 + 260 > vw ? Math.max(4, h.x - 274) : h.x + 14
  return { left: `${left}px`, top: `${h.y + 14}px` }
})
</script>

<template>
  <div v-if="store.hover.value" class="map-tooltip" :style="style" role="tooltip">
    <strong>{{ store.hover.value.title }}</strong>
    <div v-for="(l, i) in store.hover.value.lines" :key="i" class="map-tooltip-line">{{ l }}</div>
  </div>
</template>

<style scoped>
.map-tooltip {
  position: fixed; z-index: 50; max-width: 260px; pointer-events: none;
  background: var(--bg-primary, #fff); color: var(--text-primary, #1f2937);
  border: 1px solid var(--border-color, #e5e7eb); border-radius: 6px; box-shadow: var(--shadow);
  padding: 6px 8px; font-size: 12px; line-height: 1.45;
}
.map-tooltip-line { color: var(--text-secondary, #6b7280); word-break: break-all; }
</style>
