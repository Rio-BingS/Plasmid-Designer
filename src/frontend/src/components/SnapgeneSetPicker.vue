<script setup lang="ts">
/**
 * SnapGene 集合选择框：可从列表选，也可手动输入集合名称或集合 ID。
 * 原生 <input list> + <datalist>：输入时浏览器自动按关键字过滤候选。
 * 回车或失焦时解析为集合 ID；不是任何集合时当作质粒名（如 pET-30a），
 * 发出 `search` 交给页面跨集合搜索，输入框保留原文。
 */
import { ref, watch } from 'vue'
import { resolveSnapgeneSet, snapgeneSearchable, snapgeneSetLabel, type SetLike } from '../utils/snapgeneSet'

const props = defineProps<{ modelValue: string; sets: SetLike[]; inputClass?: string }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'change', v: string): void
  (e: 'search', q: string): void
}>()

const listId = `sg-sets-${Math.random().toString(36).slice(2, 8)}`
const text = ref(snapgeneSetLabel(props.modelValue, props.sets))

watch(() => [props.modelValue, props.sets] as const, ([v, sets]) => {
  // 外部改了选中值（例如首次加载默认第一项）时同步显示文字
  if (resolveSnapgeneSet(text.value, sets) !== v) text.value = snapgeneSetLabel(v, sets)
})

// 同一段文字只搜一次（回车与 change 可能先后触发）
let lastSearch = ''

function commit() {
  const raw = text.value.trim()
  const id = resolveSnapgeneSet(raw, props.sets)
  if (!id) {
    if (props.sets.length && snapgeneSearchable(raw)) {
      if (raw !== lastSearch) { lastSearch = raw; emit('search', raw) }
      return
    }
    text.value = snapgeneSetLabel(props.modelValue, props.sets)
    return
  }
  lastSearch = ''
  text.value = snapgeneSetLabel(id, props.sets)
  if (id === props.modelValue) return
  emit('update:modelValue', id)
  emit('change', id)
}

function onInput() {
  // 从候选列表点选时浏览器直接填入完整名称：立即生效，不必再按回车
  const t = text.value.trim()
  if (props.sets.some((s) => s.name === t)) commit()
}
</script>

<template>
  <input
    v-model="text"
    type="text"
    :class="inputClass"
    :list="listId"
    aria-label="SnapGene 集合或质粒名"
    placeholder="选择集合，或输入质粒名（如 pET-30a）回车跨集合搜索"
    autocomplete="off"
    @input="onInput"
    @change="commit"
    @keydown.enter.prevent="commit"
  />
  <datalist :id="listId">
    <option v-for="s in sets" :key="s.set_id" :value="s.name">{{ s.set_id }}</option>
  </datalist>
</template>
