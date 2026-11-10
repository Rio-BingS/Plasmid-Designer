<script setup lang="ts">
/**
 * 复核队列卡片（goal v3 W14，PL §F；v3.1 X2 三级聚合展示）
 *
 * 只收「人工不看无法收口」的条目：确证差异（fail）、范围内覆盖缺口
 * （read_shortfall/unknown）、未决位点（pending）——beyond_primer 只给
 * 动作建议不入队。每项可一键确认（伪影/突变，留痕 by/at）或打开证据
 * 包（位点 ±10bp 峰图 + 逐 read 调用/Q/次峰比，复用 EvidencePackPanel）。
 *
 * goal v3.1 X2：队列按三级聚合后条目数已可控（真实子集 330 → 12），
 * 前端默认展开**前 10 项**，其余折叠为「其余 N 项」（展开不丢信息）——
 * 与后端聚合配合，长队列不再铺满整屏。
 */
import { computed, ref } from 'vue'
import type { ReviewQueueItem } from '@/api'

/** 默认展开条数（goal v3.1 §3.2：每个样品默认展开前 10 项） */
const DEFAULT_VISIBLE = 10

const emit = defineEmits<{
  (e: 'confirm', refPos: number, decision: 'artifact' | 'mutation'): void
  (e: 'open-evidence', refPos: number): void
}>()

const expanded = ref(true)
const showAll = ref(false)

const props = defineProps<{
  queue: ReviewQueueItem[]
  reviewedCount: number
  busy: boolean
}>()

/** 默认只渲染前 DEFAULT_VISIBLE 项；「其余 N 项」可一键展开 */
const visibleQueue = computed(() =>
  showAll.value ? props.queue : props.queue.slice(0, DEFAULT_VISIBLE),
)
const hiddenCount = computed(() =>
  Math.max(0, props.queue.length - DEFAULT_VISIBLE),
)

const PRIO_BADGE: Record<string, { label: string; cls: string }> = {
  fail: { label: '确证差异', cls: 'bad' },
  read_shortfall: { label: '覆盖缺口', cls: 'gap' },
  pending: { label: '未决', cls: 'warn' },
}

function prioBadge(item: ReviewQueueItem): { label: string; cls: string } {
  return PRIO_BADGE[item.prio] ?? { label: item.prio, cls: 'warn' }
}

/** 聚合级别标注（goal v3.1 X2）：read 问题 / N 处合并 / 单点 */
const LEVEL_BADGE: Record<string, string> = {
  read: '整条 read',
  segment: '区段',
  site: '位点',
  confirmed: '确证',
  junction: '接合区',
}

function levelBadge(item: ReviewQueueItem): string {
  const lv = item.level ?? 'site'
  const base = LEVEL_BADGE[lv] ?? lv
  return item.sites && item.sites > 1 ? `${base} · ${item.sites} 处` : base
}

function posText(item: ReviewQueueItem): string {
  if (item.ref_start == null) return '—'
  if (item.ref_end != null && item.ref_end !== item.ref_start) {
    return `${item.ref_start}–${item.ref_end}`
  }
  return String(item.ref_start)
}
</script>

<template>
  <div class="rq-card" v-if="queue.length || reviewedCount">
    <div class="rq-head" @click="expanded = !expanded">
      <h4 class="section-title">
        复核队列
        <span class="rq-count">{{ queue.length }} 项待复核</span>
        <span v-if="reviewedCount" class="rq-reviewed">已人工确认 {{ reviewedCount }} 处</span>
      </h4>
      <button class="rq-fold" type="button">{{ expanded ? '收起' : '展开' }}</button>
    </div>
    <p class="rq-note" v-if="queue.length">
      引物够不到的缺口（需加引物）不入队列，见覆盖缺口标注；确认前请核对峰图证据
    </p>
    <ul class="rq-list" v-if="expanded">
      <li v-for="(it, i) in visibleQueue" :key="`${it.kind}-${it.ref_start}-${i}`" class="rq-item">
        <span class="rq-badge" :class="prioBadge(it).cls">{{ prioBadge(it).label }}</span>
        <span class="rq-level">{{ levelBadge(it) }}</span>
        <span class="mono rq-pos">位置 {{ posText(it) }}</span>
        <span v-if="it.element" class="rq-element">{{ it.element }}</span>
        <span class="rq-detail" :title="it.detail">{{ it.detail }}</span>
        <span v-if="it.action" class="rq-action" :title="it.action">建议：{{ it.action }}</span>
        <span class="rq-ops">
          <button class="mini-btn" :disabled="busy" @click="emit('open-evidence', it.ref_start ?? 0)">
            证据
          </button>
          <button v-if="it.ref_start != null" class="mini-btn" :disabled="busy"
                  title="峰图显示为测序伪影（压缩区/染料团等），移出阻断"
                  @click="emit('confirm', it.ref_start, 'artifact')">确认伪影</button>
          <button v-if="it.ref_start != null" class="mini-btn bad" :disabled="busy"
                  title="峰图确认为真实序列差异，按确证差异收口"
                  @click="emit('confirm', it.ref_start, 'mutation')">确认突变</button>
        </span>
      </li>
      <li v-if="hiddenCount" class="rq-more">
        <button class="mini-btn" type="button" @click="showAll = !showAll">
          {{ showAll ? '收起其余项' : `其余 ${hiddenCount} 项` }}
        </button>
      </li>
    </ul>
    <p class="rq-empty" v-else-if="reviewedCount">队列已清空——所有条目均已人工确认并留痕</p>
  </div>
</template>

<style scoped>
.rq-card { border: 1px solid var(--border, #e2e2e2); border-radius: 8px; padding: 10px 14px; margin-top: 14px; }
.rq-head { display: flex; align-items: center; justify-content: space-between; cursor: pointer; }
.rq-count { font-size: 0.8rem; color: var(--text-2, #666); font-weight: normal; margin-left: 6px; }
.rq-reviewed { font-size: 0.8rem; color: #2563eb; margin-left: 8px; }
.rq-fold { border: none; background: transparent; color: var(--text-3, #888); cursor: pointer; font-size: 0.8rem; }
.rq-note { font-size: 0.78rem; color: var(--text-3, #888); margin: 4px 0 8px; }
.rq-list { list-style: none; margin: 0; padding: 0; }
.rq-item { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; padding: 7px 0; border-top: 1px dashed var(--border, #eee); }
.rq-badge { font-size: 0.72rem; border-radius: 999px; padding: 1px 8px; white-space: nowrap; }
.rq-badge.bad { background: #fdecea; color: #c0392b; }
.rq-badge.gap { background: #fff3e0; color: #e67e22; }
.rq-badge.warn { background: #fff8e1; color: #b26a00; }
.rq-level { font-size: 0.72rem; color: var(--text-3, #888); }
.rq-more { padding: 8px 0 2px; border-top: 1px dashed var(--border, #eee); text-align: center; }
.rq-pos { font-size: 0.8rem; }
.rq-element { font-size: 0.8rem; color: #2563eb; }
.rq-detail { font-size: 0.82rem; color: var(--text-2, #555); flex: 1 1 260px; min-width: 200px; }
.rq-action { font-size: 0.78rem; color: var(--text-3, #888); }
.rq-ops { display: flex; gap: 6px; margin-left: auto; }
.rq-empty { font-size: 0.82rem; color: var(--text-3, #888); margin: 6px 0 0; }
.mini-btn.bad { color: #c0392b; }
</style>
