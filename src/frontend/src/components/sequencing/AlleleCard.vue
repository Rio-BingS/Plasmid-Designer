<script setup lang="ts">
/**
 * 混合样品解卷积结果卡（tracy）
 *
 * 从 SequencingPanel.vue 拆出的展示子组件：逐文件列出解卷积出的
 * 等位基因序列前 60bp。纯展示，无自有状态。
 */
import type { SequencingAnalysis } from '@/api'

defineProps<{
  decomposedAlleles: NonNullable<SequencingAnalysis['decomposed_alleles']>
}>()
</script>

<template>
  <div class="allele-box" v-if="Object.keys(decomposedAlleles).length">
    <h4 class="section-title">混合样品解卷积结果（tracy）</h4>
    <div v-for="(alleles, fname) in decomposedAlleles" :key="fname" class="allele-item">
      <strong>{{ fname }}</strong>:
      <span v-for="(a, i) in alleles" :key="i" class="mono allele-seq">{{ a.sequence.slice(0, 60) }}…</span>
    </div>
  </div>
</template>

<style scoped>
/* 解卷积卡样式：随模板从 SequencingPanel.vue 迁入 */
.allele-box { background: var(--bg-secondary, #f9f9f9); border-radius: 8px; padding: 0.75rem 1rem; }
.allele-item { font-size: 0.85rem; margin: 0.35rem 0; }
.allele-seq { margin: 0 0.75rem; }
.section-title { font-size: 0.95rem; margin: 0 0 0.5rem; }
.mono { font-family: Consolas, monospace; }
</style>
