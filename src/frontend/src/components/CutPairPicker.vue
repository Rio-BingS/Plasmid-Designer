<script setup lang="ts">
/**
 * 「选择所需片段」下拉：多切点酶切时列出全部可胶回收的骨架片段。
 *
 * 心智模型对齐实验操作——用户选的是「回收哪条带」（两端酶 + 长度 + 骨架
 * 是否完整），不是抽象的切点坐标。第一项 = 自动推荐（最外侧切点对）；
 * 骨架有残留的选项带 ⚠ 标注。v-model 直接绑定 cut_pair（null = 自动）。
 */
import { computed } from 'vue'
import type { CutPairOption } from '@/types'

const props = defineProps<{ options: CutPairOption[] }>()
const model = defineModel<[number, number] | null>({ required: true })

const items = computed(() => props.options.map((o, i) => ({
  value: `${o.left_cut}-${o.right_cut}`,
  pair: [o.left_cut, o.right_cut] as [number, number],
  label: `回收 ${o.left_enzyme}(${o.left_cut}) × ${o.right_enzyme}(${o.right_cut})`
    + ` · ${o.kept_bp} bp` + (o.leftover_cuts ? ` · ⚠ 骨架残留 ${o.leftover_cuts} 切点` : ' · 骨架完整'),
  recommended: i === 0,
})))

const selected = computed({
  get: () => (model.value ? `${model.value[0]}-${model.value[1]}` : 'auto'),
  set: (v: string) => {
    if (v === 'auto') model.value = null
    else {
      const it = items.value.find((x) => x.value === v)
      if (it) model.value = it.pair
    }
  },
})
</script>

<template>
  <div v-if="options.length > 1" class="cut-picker" data-testid="cut-pair-picker">
    <label class="cut-picker-label" for="cut-pair-select">回收片段</label>
    <select id="cut-pair-select" v-model="selected" class="cut-picker-select">
      <option value="auto">自动（推荐：最外侧切点对，骨架完整）</option>
      <option v-for="it in items" :key="it.value" :value="it.value">
        {{ it.label }}{{ it.recommended ? '（推荐）' : '' }}
      </option>
    </select>
  </div>
</template>

<style scoped>
.cut-picker {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0.35rem 0 0.1rem;
}

.cut-picker-label {
  font-size: 0.8rem;
  font-weight: 600;
  color: #2d3748;
  white-space: nowrap;
}

.cut-picker-select {
  flex: 1;
  min-width: 0;
  font-size: 0.8rem;
  padding: 0.25rem 0.4rem;
  border: 1px solid #cbd5e0;
  border-radius: 0.375rem;
  background: #fff;
  color: #2d3748;
}
</style>
