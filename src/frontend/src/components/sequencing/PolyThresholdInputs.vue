<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  DEFAULT_POLY_THRESHOLDS, loadPolyThresholds, savePolyThresholds,
  validatePolyThresholds, type PolyThresholds,
} from '@/utils/polyThresholds'

/**
 * poly「大量缺失」阈值设置（二期 10d）。改动即存本地，之后的分析自动带上。
 * 传入 analysisId 时显示「按此阈值重新判定」按钮（只重跑判定，不重新比对）。
 */
const props = defineProps<{ analysisId?: string; busy?: boolean; applied?: PolyThresholds | null }>()
const emit = defineEmits<{ (e: 'rejudge', t: PolyThresholds): void }>()

const th = ref<PolyThresholds>(loadPolyThresholds())
const error = computed(() => validatePolyThresholds(th.value))
const differsFromApplied = computed(() => {
  const a = props.applied
  if (!a) return false
  const t = th.value
  return a.count !== t.count || a.pct !== t.pct
    || a.count_enabled !== t.count_enabled || a.pct_enabled !== t.pct_enabled
})

function update(patch: Partial<PolyThresholds>) {
  th.value = { ...th.value, ...patch }
  if (!error.value) savePolyThresholds(th.value)
}
function setNum(key: 'count' | 'pct', ev: Event) {
  const v = Number((ev.target as HTMLInputElement).value)
  update({ [key]: Number.isFinite(v) ? (key === 'count' ? Math.round(v) : v)
                                     : DEFAULT_POLY_THRESHOLDS[key] })
}
function reset() { update({ ...DEFAULT_POLY_THRESHOLDS }) }
</script>

<template>
  <fieldset class="poly-th" data-testid="poly-thresholds">
    <legend>poly 大量缺失报警（任一条达到即报 ✗）</legend>
    <label>
      <input type="checkbox" :checked="th.count_enabled" data-testid="poly-th-count-on"
             @change="update({ count_enabled: ($event.target as HTMLInputElement).checked })" />
      确证缺失 ≥
      <input type="number" min="1" step="1" :value="th.count" :disabled="!th.count_enabled"
             data-testid="poly-th-count" @change="setNum('count', $event)" /> 个碱基
    </label>
    <label>
      <input type="checkbox" :checked="th.pct_enabled" data-testid="poly-th-pct-on"
             @change="update({ pct_enabled: ($event.target as HTMLInputElement).checked })" />
      或 缺失占该 poly 长度 ≥
      <input type="number" min="0.1" max="100" step="0.1" :value="th.pct" :disabled="!th.pct_enabled"
             data-testid="poly-th-pct" @change="setNum('pct', $event)" /> %
    </label>
    <p v-if="!th.count_enabled && !th.pct_enabled" class="poly-th-note">两条都已取消：不做大量缺失报警</p>
    <button type="button" class="poly-th-btn" @click="reset">恢复默认（5 个 / 15%）</button>
    <button v-if="props.analysisId" type="button" class="poly-th-btn"
            :disabled="!!error || props.busy" data-testid="poly-th-rejudge"
            @click="emit('rejudge', { ...th })">
      {{ props.busy ? '重新判定中…' : (differsFromApplied ? '按此阈值重新判定当前结果' : '重新判定当前结果') }}
    </button>
    <p v-if="error" class="poly-th-error">{{ error }}</p>
  </fieldset>
</template>

<style scoped>
.poly-th { border: 1px solid var(--border-color, #ddd); border-radius: 6px; padding: 0.4rem 0.6rem; margin: 0.4rem 0; }
.poly-th legend { font-size: 0.8rem; padding: 0 0.25rem; }
.poly-th label { display: block; margin: 0.3rem 0; }
.poly-th input[type='number'] { width: 4.5rem; margin: 0 0.25rem; }
.poly-th-btn { font-size: 0.8rem; margin: 0.2rem 0.4rem 0 0; }
.poly-th-note { font-size: 0.8rem; color: #8a6d3b; margin: 0.2rem 0; }
.poly-th-error { color: #c0392b; font-size: 0.8rem; margin: 0.2rem 0 0; }
</style>
