<script setup lang="ts">
/**
 * 酶自动补全选择器（可复用）
 *
 * - 输入即过滤（大小写不敏感的子串匹配，如 "nde" → NdeI / NdeII）
 * - 下拉项显示「酶名 [识别序列]」，匹配部分下划线高亮
 * - 单选模式（multiple=false）：v-model 为酶名字符串
 * - 多选模式（multiple=true）：v-model 为酶名数组，已选项显示为可移除的 chip
 * - 支持键盘 ↑↓ 选择、Enter 确认、Esc 关闭
 *
 * enzymes 传 Record<名称, { recognition_sequence }>（可显示识别序列）
 * 或纯名称数组（不显示识别序列）。
 */
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'

interface EnzymeMeta { recognition_sequence?: string }

const props = withDefaults(defineProps<{
  enzymes: Record<string, EnzymeMeta> | string[]
  modelValue: string | string[]
  multiple?: boolean
  placeholder?: string
}>(), {
  multiple: false,
  placeholder: '输入以搜索酶…',
})

const emit = defineEmits<{ (e: 'update:modelValue', v: string | string[]): void }>()

const names = computed<string[]>(() =>
  Array.isArray(props.enzymes) ? props.enzymes : Object.keys(props.enzymes)
)
const recogOf = (name: string): string =>
  Array.isArray(props.enzymes) ? '' : (props.enzymes[name]?.recognition_sequence || '')

const selectedNames = computed<string[]>(() =>
  Array.isArray(props.modelValue) ? props.modelValue : []
)

const query = ref('')
const open = ref(false)
const highlight = ref(0)
const rootEl = ref<HTMLElement | null>(null)

const filtered = computed<string[]>(() => {
  const q = query.value.trim().toLowerCase()
  const base = names.value
  // 每输入一个字符实时过滤：按酶名或识别序列匹配
  const hits = q
    ? base.filter(n =>
        n.toLowerCase().includes(q) || recogOf(n).toLowerCase().includes(q)
      )
    : base
  return hits.slice(0, 50)
})

watch(() => props.modelValue, (v) => {
  if (!props.multiple) query.value = (v as string) || ''
}, { immediate: true })

watch(query, (q) => {
  highlight.value = 0
  // 单选模式：清空输入即取消选择（回退到父组件的默认/自动逻辑）
  if (!props.multiple && !q.trim() && props.modelValue) {
    emit('update:modelValue', '')
  }
})

function choose(name: string) {
  if (props.multiple) {
    if (!selectedNames.value.includes(name)) {
      emit('update:modelValue', [...selectedNames.value, name])
    }
    query.value = ''
    highlight.value = 0
    // 保持下拉打开，便于连续添加
  } else {
    emit('update:modelValue', name)
    query.value = name
    open.value = false
  }
}

function removeChip(name: string) {
  if (!props.multiple) return
  emit('update:modelValue', selectedNames.value.filter(n => n !== name))
}

function onKeydown(e: KeyboardEvent) {
  if (!open.value) {
    if (['ArrowDown', 'ArrowUp', 'Enter'].includes(e.key)) open.value = true
    return
  }
  if (e.key === 'ArrowDown') {
    highlight.value = Math.min(highlight.value + 1, filtered.value.length - 1)
    e.preventDefault()
  } else if (e.key === 'ArrowUp') {
    highlight.value = Math.max(highlight.value - 1, 0)
    e.preventDefault()
  } else if (e.key === 'Enter') {
    const name = filtered.value[highlight.value]
    if (name) choose(name)
    e.preventDefault()
  } else if (e.key === 'Escape') {
    open.value = false
  }
}

function onClickOutside(e: MouseEvent) {
  if (rootEl.value && !rootEl.value.contains(e.target as Node)) open.value = false
}
onMounted(() => document.addEventListener('click', onClickOutside))
onBeforeUnmount(() => document.removeEventListener('click', onClickOutside))

function highlightMatch(name: string): string {
  const q = query.value.trim()
  if (!q) return name
  const idx = name.toLowerCase().indexOf(q.toLowerCase())
  if (idx < 0) return name
  return `${name.slice(0, idx)}<u>${name.slice(idx, idx + q.length)}</u>${name.slice(idx + q.length)}`
}
</script>

<template>
  <div class="es-root" ref="rootEl">
    <div class="es-box" :class="{ open }" @click="open = true">
      <template v-if="multiple">
        <span
          v-for="name in selectedNames"
          :key="name"
          class="es-chip"
          @click.stop
        >
          {{ name }}
          <button class="es-chip-x" title="移除" @click.stop="removeChip(name)">×</button>
        </span>
      </template>
      <input
        class="es-input"
        v-model="query"
        :placeholder="multiple && selectedNames.length ? '继续添加…' : placeholder"
        @focus="open = true"
        @input="open = true"
        @keydown="onKeydown"
      />
    </div>

    <ul v-if="open && filtered.length" class="es-dropdown">
      <li
        v-for="(n, i) in filtered"
        :key="n"
        class="es-option"
        :class="{ hl: i === highlight, selected: selectedNames.includes(n) }"
        @mousedown.prevent="choose(n)"
        @mouseenter="highlight = i"
      >
        <span class="es-name" v-html="highlightMatch(n)"></span>
        <span v-if="recogOf(n)" class="es-site">[{{ recogOf(n) }}]</span>
      </li>
    </ul>
    <div v-else-if="open" class="es-empty">无匹配的酶</div>
  </div>
</template>

<style scoped>
.es-root {
  position: relative;
  width: 100%;
}

.es-box {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem;
  min-height: 2.4rem;
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: white;
  cursor: text;
}

.es-box.open {
  border-color: var(--primary-color);
}

.es-input {
  flex: 1;
  min-width: 5rem;
  border: none;
  outline: none;
  font-size: 0.875rem;
  padding: 0.25rem 0.15rem;
  background: transparent;
}

.es-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.15rem 0.3rem 0.15rem 0.55rem;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 9999px;
  font-size: 0.78rem;
}

.es-chip-x {
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.85rem;
  line-height: 1;
  color: var(--text-secondary);
  padding: 0 0.15rem;
}

.es-chip-x:hover {
  color: #dc2626;
}

.es-dropdown {
  position: absolute;
  z-index: 50;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  max-height: 16rem;
  overflow-y: auto;
  margin: 0;
  padding: 0.25rem;
  list-style: none;
  background: white;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.12);
}

.es-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.85rem;
}

.es-option.hl {
  background: var(--bg-secondary);
}

.es-option.selected {
  opacity: 0.55;
}

.es-name u {
  text-decoration: underline;
  font-weight: 600;
}

.es-site {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  color: var(--text-secondary);
  white-space: nowrap;
}

.es-empty {
  padding: 0.5rem 0.6rem;
  font-size: 0.8rem;
  color: var(--text-secondary);
}
</style>
