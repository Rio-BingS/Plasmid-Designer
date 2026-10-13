<script setup lang="ts">
import { computed } from 'vue'
import { DEFAULT_RELIABLE_ZONE, validateReliableZone, type ReliableZone } from '@/utils/reliableZone'

const zone = defineModel<ReliableZone>({ required: true })

const error = computed(() => validateReliableZone(zone.value))

function set(key: keyof ReliableZone, ev: Event) {
  const v = Number((ev.target as HTMLInputElement).value)
  zone.value = { ...zone.value, [key]: Number.isFinite(v) ? Math.round(v) : DEFAULT_RELIABLE_ZONE[key] }
}
function reset() { zone.value = { ...DEFAULT_RELIABLE_ZONE } }
</script>

<template>
  <fieldset class="zone-inputs" data-testid="reliable-zone">
    <legend>read 可信区（read 坐标 bp）</legend>
    <label>首端硬不可信区 1–<input type="number" min="0" :value="zone.frontHard" data-testid="zone-front-hard" @change="set('frontHard', $event)" /></label>
    <label>首端灰区（按 Q 值与峰型判定）至 <input type="number" min="0" :value="zone.frontGray" data-testid="zone-front-gray" @change="set('frontGray', $event)" /></label>
    <label>尾端灰区（默认不可信，靠其他引物覆盖）自 <input type="number" min="0" :value="zone.tailGray" data-testid="zone-tail-gray" @change="set('tailGray', $event)" /> 之后</label>
    <label>尾端硬不可信区 <input type="number" min="0" :value="zone.tailHard" data-testid="zone-tail-hard" @change="set('tailHard', $event)" /> 之后</label>
    <button type="button" class="zone-reset" @click="reset">恢复默认（30 / 60 / 700 / 900）</button>
    <p v-if="error" class="zone-error" data-testid="zone-error">{{ error }}</p>
  </fieldset>
</template>

<style scoped>
.zone-inputs { border: 1px solid var(--border-color, #ddd); border-radius: 6px; padding: 0.4rem 0.6rem; margin: 0.4rem 0; }
.zone-inputs legend { font-size: 0.8rem; padding: 0 0.25rem; }
.zone-inputs label { display: block; margin: 0.3rem 0; }
.zone-inputs input { width: 5rem; margin-left: 0.25rem; }
.zone-reset { font-size: 0.8rem; margin-top: 0.2rem; }
.zone-error { color: #c0392b; font-size: 0.8rem; margin: 0.2rem 0 0; }
</style>
