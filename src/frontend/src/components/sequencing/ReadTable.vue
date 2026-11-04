<script setup lang="ts">
/**
 * Read 摘要表
 *
 * 从 SequencingPanel.vue 拆出的展示子组件：每条 read 的方向/落点/修剪/
 * Q/质量评级/一致性一行，与读取失败错误行。查看按钮只上报事件，
 * 峰图联动逻辑（自动显示、选中、滚动）留在父组件。
 */
import { computed } from 'vue'
import type { SequencingAnalysis } from '@/api'
import { readSpanText } from '@/utils/seqPanelModel'
import { cdsRelation, type CdsTarget } from '@/utils/cdsMode'

type ReadRow = SequencingAnalysis['reads'][number]

const props = defineProps<{
  reads: ReadRow[]
  /** 后端逐 read 解析/分析失败的错误列表 */
  errors: { filename: string; error: string }[]
  /** 目的基因模式的目标 CDS（伪特征）；不传/为 null → 不显示「目的CDS」列 */
  cdsFeature?: CdsTarget | null
}>()

/** 可信区（原始 read 坐标）；旧记录无此字段时显示 - */
function zoneText(r: ReadRow): string {
  const z = r.reliable_zone
  if (!z) return '-'
  return z.start > z.end ? '无' : `${z.raw_start}–${z.raw_end}`
}
function zoneTitle(r: ReadRow): string | undefined {
  const z = r.reliable_zone
  if (!z) return undefined
  return [z.front_reason, z.tail_reason].filter(Boolean).join('；') || undefined
}

/** read ↔ 目的 CDS 关系（每行求交；无目标时为 null，列隐藏） */
const relations = computed(() => {
  if (!props.cdsFeature) return null
  return new Map(props.reads.map((r) => [r.index, cdsRelation(r, props.cdsFeature!)]))
})

function relationText(rel: NonNullable<ReturnType<typeof cdsRelation>>): string {
  if (!rel.covers) return '未覆盖'
  return `覆盖 ${rel.percent.toFixed(0)}%`
}
function relationTitle(rel: ReturnType<typeof cdsRelation>,
                       cds: CdsTarget): string | undefined {
  if (!rel.covers) return 'read 比对区间与目的 CDS 无交集（不参与目的 CDS 结论）'
  return `CDS 内 ${rel.overlapStart! - cds.start + 1}–${rel.overlapEnd! - cds.start + 1}`
    + `（${rel.coveredBp} bp）`
}

const emit = defineEmits<{
  (e: 'view', index: number): void
}>()
</script>

<template>
  <div>
    <table class="seq-table" v-if="reads.length">
      <thead>
        <tr><th>文件</th><th>方向</th><th>比对区间</th><th>修剪后</th><th>可信区</th><th>平均Q</th><th>质量</th><th>一致性</th><th v-if="relations">目的CDS</th><th>证据查看</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in reads" :key="r.index">
          <td>{{ r.filename }}</td>
          <td>{{ r.direction === '+' ? '正向' : '反向' }}</td>
          <td :title="r.wraps_origin ? '跨越环状参考原点' : undefined">{{ readSpanText(r, ' - ') }}</td>
          <td>{{ r.trimmed_length }} bp</td>
          <td :title="zoneTitle(r)" data-testid="read-zone">{{ zoneText(r) }}</td>
          <td>{{ r.mean_q }}</td>
          <td>
            <span v-if="r.grade" class="grade-chip" :class="'grade-' + r.grade"
                  :title="r.q20_ratio != null ? `Q20 比例 ${(r.q20_ratio * 100).toFixed(0)}%` : ''">
              {{ r.grade }}
            </span>
            <span v-else>-</span>
          </td>
          <td>{{ (r.identity * 100).toFixed(1) }}%</td>
          <td v-if="relations">
            <span v-if="relations.get(r.index)" class="cds-chip"
                  :class="relations.get(r.index)!.covers ? 'cds-covers' : 'cds-off'"
                  :title="relationTitle(relations.get(r.index)!, cdsFeature!)">
              {{ relationText(relations.get(r.index)!) }}
            </span>
            <span v-else>-</span>
          </td>
          <td>
            <button class="mini-btn" @click="emit('view', r.index)">查看</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-if="errors.length" class="error-msg">
      {{ errors.map((e) => `${e.filename}: ${e.error}`).join('；') }}
    </p>
  </div>
</template>

<style scoped>
/* read 摘要表样式：随模板从 SequencingPanel.vue 迁入 */
.seq-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.seq-table th, .seq-table td { padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border-color, #eee); text-align: left; }
.seq-table th { background: var(--bg-secondary, #f7f7f7); }
.grade-chip { display: inline-block; min-width: 20px; text-align: center; font-weight: 700; border-radius: 6px; padding: 1px 7px; font-size: 0.8rem; }
.grade-A { background: #E5F5E9; color: #227A36; }
.grade-B { background: #FCF3DC; color: #9A6D00; }
.grade-C { background: #FBEAE8; color: #A03227; }
.mini-btn {
  border: 1px solid var(--border-color, #ddd); background: #fff; border-radius: 4px;
  font-size: 0.78rem; padding: 0.2rem 0.6rem; cursor: pointer; margin-left: 0.25rem;
}
.mini-btn:hover { background: var(--bg-secondary, #f5f5f5); }
.cds-chip { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 0.75rem; }
.cds-covers { background: #E8F6EC; color: #2E9E44; }
.cds-off { background: #F3F4F6; color: #6B7280; }
.error-msg { color: #c0392b; font-size: 0.85rem; margin-top: 0.5rem; }
</style>
