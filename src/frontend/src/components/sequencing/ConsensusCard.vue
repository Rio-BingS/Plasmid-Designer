<script setup lang="ts">
/**
 * 共识序列卡（拼接结果 + 导出）
 *
 * 从 SequencingPanel.vue 拆出的子组件：共识序列分段渲染（与参考差异
 * 位黄色高亮）与 FASTA/GenBank 导出。导出走父组件注入的回调——
 * exportConsensus 需要 analysis_id，下载动作与后端调用细节留在父组件，
 * 本组件只管「仅导出实测覆盖区」开关（coveredOnly 由父组件持有默认值）。
 *
 * 拼接结果默认只显示 read 实测拼出的片段（consensus.segments）：全长共识为了
 * 坐标对齐，用参考碱基填充了未测位置，整段显示时看起来就是参考序列。
 * 关掉「仅实测覆盖区」时显示全长，未测的参考填充以灰色区分。
 */
import { computed } from 'vue'
import type { SequencingAnalysis } from '@/api'

const props = defineProps<{
  analysis: SequencingAnalysis
  /** 默认 true（部分测序是常规策略：只导出 read 实测覆盖区域） */
  coveredOnly?: boolean
}>()

const coveredOnly = defineModel<boolean>('coveredOnly', { default: true })

const emit = defineEmits<{
  (e: 'download', format: string): void
}>()

type Piece = { text: string; diff: boolean; filled: boolean }

/** 共识下标 → 是否为 read 实测（旧记录无 segments 时全部视为实测，保持原显示） */
const segments = computed(() => props.analysis.consensus.segments ?? null)
const measured = computed(() => {
  const segs = segments.value
  if (!segs) return null
  const mask = new Uint8Array(props.analysis.consensus.sequence.length)
  for (const g of segs) mask.fill(1, g.cons_start - 1, g.cons_end)
  return mask
})
const assembledLen = computed(() =>
  props.analysis.consensus.assembled_length
  ?? segments.value?.reduce((n, g) => n + g.sequence.length, 0)
  ?? props.analysis.consensus.sequence.length)

const diffAt = computed(() => {
  const s = new Set<number>()
  for (const d of props.analysis.consensus.diffs || []) {
    if (d.cons_index != null) s.add(d.cons_index)
  }
  return s
})

/** 把共识序列 [from, to)（0 起始）切成高亮/填充分段 */
function pieces(from: number, to: number): Piece[] {
  const seq = props.analysis.consensus.sequence
  const mask = measured.value
  const out: Piece[] = []
  for (let i = from; i < to; i++) {
    const d = diffAt.value.has(i)
    const f = mask ? mask[i] === 0 : false
    const last = out[out.length - 1]
    if (last && last.diff === d && last.filled === f) last.text += seq[i]
    else out.push({ text: seq[i], diff: d, filled: f })
  }
  return out
}

/** 仅实测：每个片段一块；否则全长一块 */
const blocks = computed(() => {
  const segs = segments.value
  if (coveredOnly.value && segs) {
    return segs.map((g, i) => ({
      key: `${g.cons_start}`,
      title: `片段 ${i + 1}/${segs.length} · 参考 ${g.ref_start}–${g.ref_end} · ${g.sequence.length} bp`,
      pieces: pieces(g.cons_start - 1, g.cons_end),
    }))
  }
  return [{ key: 'all', title: '', pieces: pieces(0, props.analysis.consensus.sequence.length) }]
})
const hasFilled = computed(() => !!measured.value && measured.value.includes(0))
</script>

<template>
  <div class="consensus-box">
    <div class="trace-toolbar">
      <h4 class="section-title">
        拼接结果（测序实测 {{ assembledLen }} bp<template v-if="segments">，{{ segments.length }} 段</template>）
      </h4>
      <div>
        <label class="cov-only-toggle" title="只显示/导出 read 实测拼出的区域（FASTA 按段、GenBank 未测位置 N 屏蔽）；关闭后显示全长，未测位置为参考填充">
          <input type="checkbox" v-model="coveredOnly">仅实测覆盖区
        </label>
        <button class="mini-btn" @click="emit('download', 'fasta')">导出 FASTA</button>
        <button class="mini-btn" @click="emit('download', 'genbank')">导出 GenBank</button>
      </div>
    </div>
    <p v-if="segments && !segments.length" class="hint">没有 read 比对到参考上，无法拼接。</p>
    <div v-for="b in blocks" :key="b.key" class="cons-block">
      <div v-if="b.title" class="cons-seg-title">{{ b.title }}</div>
      <pre class="consensus-pre"><span v-for="(s, i) in b.pieces" :key="i" :class="{ 'cons-diff': s.diff, 'cons-filled': s.filled }">{{ s.text }}</span></pre>
    </div>
    <p v-if="!coveredOnly && hasFilled" class="hint cons-hint">
      灰色 = 未测位置，用参考序列填充以保持坐标对齐，不是测序结果
    </p>
    <p v-if="analysis.consensus.diffs?.length" class="hint cons-hint">
      黄色高亮 = 共识序列与参考不同的位点（由测序证据投票写入，点击上方差异明细可核对峰图与比对）
    </p>
  </div>
</template>

<style scoped>
/* 共识卡样式：随模板从 SequencingPanel.vue 迁入 */
.consensus-box { background: #fff; border: 1px solid var(--border-color, #eee); border-radius: 10px; padding: 0.75rem 1rem; }
.trace-toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.35rem; }
.section-title { font-size: 0.95rem; margin: 0 0 0.5rem; }
.hint { color: #999; font-size: 0.85rem; }
.mini-btn {
  border: 1px solid var(--border-color, #ddd); background: #fff; border-radius: 4px;
  font-size: 0.78rem; padding: 0.2rem 0.6rem; cursor: pointer; margin-left: 0.25rem;
}
.mini-btn:hover { background: var(--bg-secondary, #f5f5f5); }
.cov-only-toggle {
  font-size: 0.78rem; margin-right: 0.5rem; cursor: pointer;
  display: inline-flex; align-items: center; gap: 0.25rem;
  vertical-align: middle; color: var(--text-secondary, #555);
}
.cons-block + .cons-block { margin-top: 0.5rem; }
.cons-seg-title { font-size: 0.75rem; color: var(--text-secondary, #666); margin-bottom: 0.2rem; }
.consensus-pre {
  font-family: Consolas, monospace; font-size: 0.72rem; line-height: 1.5;
  background: var(--bg-secondary, #f9f9f9); padding: 0.75rem; border-radius: 6px;
  max-height: 260px; overflow: auto; white-space: pre-wrap; word-break: break-all;
}
.cons-diff { background: #FFF3B8; border-radius: 2px; padding: 0 1px; }
.cons-filled { color: #c0c4cc; }
.cons-hint { margin-top: 0.4rem; }
</style>
