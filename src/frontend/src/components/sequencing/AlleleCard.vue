<script setup lang="ts">
/**
 * 混合样品解卷积结果卡（tracy）
 *
 * 从 SequencingPanel.vue 拆出的展示子组件：逐文件列出解卷积出的
 * 等位基因序列前 60bp 与各自 CDS 判读（goal T10；A 3.6——解卷积
 * 直接回答「主/次克隆各自是否正确」）。纯展示，无自有状态。
 */
import type { SequencingAnalysis } from '@/api'

defineProps<{
  decomposedAlleles: NonNullable<SequencingAnalysis['decomposed_alleles']>
}>()

/** 等位内各 CDS 的一句话判读（无报告/未覆盖降级展示） */
function cdsText(a: { cds_reports?: { name: string; verdict: string; coverage_status: string }[] }): string {
  const rs = a.cds_reports || []
  if (!rs.length) return ''
  return rs.map(c => `${c.name}：${c.verdict}`).join('；')
}
</script>

<template>
  <div class="allele-box" v-if="Object.keys(decomposedAlleles).length">
    <h4 class="section-title">混合样品解卷积结果（tracy）</h4>
    <div v-for="(alleles, fname) in decomposedAlleles" :key="fname" class="allele-item">
      <strong>{{ fname }}</strong>:
      <div v-for="(a, i) in alleles" :key="i" class="allele-row">
        <span class="mono allele-seq">{{ a.sequence.slice(0, 60) }}…</span>
        <span v-if="cdsText(a)" class="allele-cds" :class="{ bad: a.cds_reports?.some(c => c.coverage_status !== 'uncovered') }">{{ cdsText(a) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 解卷积卡样式：随模板从 SequencingPanel.vue 迁入 */
.allele-box { background: var(--bg-secondary, #f9f9f9); border-radius: 8px; padding: 0.75rem 1rem; }
.allele-item { font-size: 0.85rem; margin: 0.35rem 0; }
.allele-row { margin: 0.25rem 0 0.25rem 0.75rem; }
.allele-seq { margin-right: 0.75rem; }
.allele-cds { color: #2a6f2a; }
.allele-cds.bad { color: #a12622; }
.section-title { font-size: 0.95rem; margin: 0 0 0.5rem; }
.mono { font-family: Consolas, monospace; }
</style>
