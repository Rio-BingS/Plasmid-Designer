<script setup lang="ts">
/**
 * SnapGene 集合选择框：可从列表选，也可手动输入集合名称或集合 ID。
 * 原生 <input list> + <datalist>：输入时浏览器自动按关键字过滤候选。
 * 回车或失焦时解析为集合 ID，解析不出时保留原文交给后端校验（后端返回 422 提示）。
 */
import { ref, watch } from 'vue'
import { resolveSnapgeneSet, snapgeneSetLabel, type SetLike } from '../utils/snapgeneSet'

const props = defineProps<{ modelValue: string; sets: SetLike[]; inputClass?: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: string): void; (e: 'change', v: string): void }>()

const listId = `sg-sets-${Math.random().toString(36).slice(2, 8)}`
const text = ref(snapgeneSetLabel(props.modelValue, props.sets))

watch(() => [props.modelValue, props.sets] as const, ([v, sets]) => {
  // 外部改了选中值（例如首次加载默认第一项）时同步显示文字
  if (resolveSnapgeneSet(text.value, sets) !== v) text.value = snapgeneSetLabel(v, sets)
})

function commit() {
  const id = resolveSnapgeneSet(text.value, props.sets)
  if (!id) { text.value = snapgeneSetLabel(props.modelValue, props.sets); return }
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
    aria-label="SnapGene 集合（可输入名称或集合 ID）"
    placeholder="输入或选择集合，如 Yeast Plasmids / yeast_plasmids"
    autocomplete="off"
    @input="onInput"
    @change="commit"
    @keydown.enter.prevent="commit"
  />
  <datalist :id="listId">
    <option v-for="s in sets" :key="s.set_id" :value="s.name">{{ s.set_id }}</option>
  </datalist>
</template>
