<script setup lang="ts">
/**
 * poly 同聚物 / 重复结构卡片
 *
 * 从 SequencingPanel.vue 拆出的子组件：结构识别列表、显示阈值筛选、
 * 主判读（峰图计数/宽度法口径）、逐 read 判读行与 Step B 判定依据。
 * 纯展示组件——homopolymers 数据进、判读文案渲染出，无自有业务状态
 * （阈值 polyMinLen 是纯 UI 状态，留在本组件内）。
 */
import { ref, computed } from 'vue'
import type { SequencingAnalysis } from '@/api'

type Hp = NonNullable<SequencingAnalysis['homopolymers']>[number]

const props = defineProps<{
  /** 同聚物/重复结构列表（后端 homopolymers 字段） */
  homopolymers: Hp[]
}>()

// 显示阈值：默认 20bp（=后端 HOMOPOLYMER_MIN），只显示需要核对重复数的
// poly 功能结构；8-19bp 观察级同聚物与二/三核苷酸重复默认隐藏，可调低查看
const polyMinLen = ref(20)
function onPolyThresh(e: Event) {
  polyMinLen.value = Number((e.target as HTMLSelectElement).value)
}
const polyEntries = computed(() =>
  props.homopolymers.filter((h) => (h.length ?? h.end - h.start + 1) >= polyMinLen.value))
const polyHiddenCount = computed(() => props.homopolymers.length - polyEntries.value.length)

/** 结构名：period=1 用 poly(A)；period>1 是二/三核苷酸重复，按 (AT)×4 标注，
 *  避免把「参考 4 个单元」误读成 4bp 的 polyA */
function polyName(h: Hp): string {
  if ((h.period ?? 1) === 1) return `poly(${h.base})`
  return `(${h.unit})×${h.ref_repeat_count} 重复`
}
function polyUnitLabel(h: Hp): string {
  return (h.period ?? 1) === 1 ? `个 ${h.base}` : `个 ${h.unit} 单元`
}

const LENGTH_METHOD_LABELS: Record<string, string> = {
  peaks: '峰数法', width: '宽度法', second_derivative: '二阶导数法',
}
/** 信号解卷积的长度估计：method=peaks 时估计就是可分辨峰数，与主判读
 *  重复，不再展示；宽度法/二阶导数是独立口径，保留 */
function polyEstNote(h: Hp): string | null {
  if (h.length_estimate == null || h.length_method === 'peaks') return null
  const label = LENGTH_METHOD_LABELS[h.length_method ?? ''] ?? '信号估计'
  const ci = h.length_ci ? `（区间 ${h.length_ci[0]}–${h.length_ci[1]}）` : ''
  return `${label}约 ${h.length_estimate} ${polyUnitLabel(h)}${ci}`
}

/** 引物覆盖行：每条 read 的覆盖段与段内调用数/峰数（B4：截断 read 照常
 *  参与核对，只对覆盖段负责） */
function readCountLabel(rc: NonNullable<Hp['read_counts']>[number]): string {
  const d = rc.direction === '-' ? '反向' : '正向'
  const called = rc.called_count ?? '?'
  const pk = rc.peak_count ?? '?'
  if (rc.coverage === 'partial' && rc.covered_span) {
    return `${rc.filename}${d}覆盖段${rc.covered_span[0]}-${rc.covered_span[1]}调用${called}/峰${pk}`
  }
  return `${rc.filename}${d}调用${called}/峰${pk}`
}

/** 综合评判标签（Step B 三态/五档）：verdict 缺失（旧记录）回退本地推断 */
const RUN_VERDICT_LABELS: Record<string, string> = {
  accepted: '峰图计数确证（可分辨 read 互证一致）',
  caller_only: '峰图与调用一致',
  deficit_observed: '可见缺失证据，以峰图为准（计数不确证）',
  contradictory: '可分辨 read 之间计数互证矛盾，不可信',
  undetermined: '峰合并不可分辨，重复数不可判定',
}
function runVerdictLabel(h: Hp): string {
  if (h.run_verdict && RUN_VERDICT_LABELS[h.run_verdict]) {
    return RUN_VERDICT_LABELS[h.run_verdict]
  }
  return h.count_reliable ? '峰图与调用一致' : '峰图证据与调用不一致，建议核对峰图'
}

/** 判定依据行：投票 read 明细（含联合覆盖段的拼接读数） */
function verdictBasis(h: Hp): string {
  const parts: string[] = []
  for (const v of h.verdict_votes ?? []) {
    if (v.joint) {
      const reads = (v.reads ?? []).map((r) => `${r.filename} 峰${r.peak_count ?? '?'}`).join('＋')
      parts.push(`联合覆盖拼接 ${v.observed_total ?? '?'} 个（${reads}）`)
    } else {
      const d = v.direction === '-' ? '反向' : '正向'
      const anc = v.anchor && v.anchor !== 'reliable' ? '（锚定一般）' : ''
      parts.push(`${v.filename}${d}峰 ${v.peak_count ?? '?'} 个${anc}`)
    }
  }
  return parts.join('；') || '—'
}
function voteCounts(h: Hp): string {
  return (h.verdict_votes ?? [])
    .filter((v) => !v.joint)
    .map((v) => `${v.filename}峰${v.peak_count ?? '?'}`)
    .join(' / ') || '—'
}

/** 逐引物判读行：优先用后端 Step A 的 resolution 判读（resolvable 峰图
 *  证据完整 / merged 峰合并不可判定 / deficit 可见缺失 / noisy 肩峰）；
 *  无 resolution 字段的旧记录回退本地推断 */
const ANCHOR_LABELS: Record<string, string> = {
  marginal: '可信度一般（路标在信号边缘区）',
  unreliable: '不可靠（路标踩信号异常区）',
  'one-sided': '单侧锚定',
}
function readVerdictLine(rc: NonNullable<Hp['read_counts']>[number]): string {
  const d = rc.direction === '-' ? '反向' : '正向'
  const called = rc.called_count ?? '?'
  const pk = rc.peak_count ?? '?'
  const seg = rc.coverage === 'partial' && rc.covered_span
    ? `仅覆盖该结构 ${rc.covered_span[0]}-${rc.covered_span[1]}，未完整跨过（read 在结构内截断/起始）——覆盖段峰图已参与核对`
    : '完整跨过该结构'
  const anchor = rc.anchor_grade && rc.anchor_grade !== 'reliable'
    ? `；锚定${ANCHOR_LABELS[rc.anchor_grade] ?? rc.anchor_grade}`
    : ''
  if (rc.resolution === 'resolvable') {
    return `${rc.filename}（${d}）：调用 ${called}，可分辨峰 ${pk} → 峰图与调用一致，计数确证；${seg}${anchor}`
  }
  if (rc.resolution === 'merged') {
    return `${rc.filename}（${d}）：峰合并不可分辨（${rc.reason || '无独立可辨峰'}）→ 该 read 对此结构不可判定${rc.length_estimate != null ? `，宽度法估计 ${rc.length_estimate} 仅供参考` : ''}；${seg}${anchor}`
  }
  if (rc.resolution === 'deficit') {
    const gap = typeof called === 'number' && typeof pk === 'number' ? called - pk : '?'
    return `${rc.filename}（${d}）：调用 ${called}，可分辨峰 ${pk} → 可见缺失 ${gap} 个（以峰图为准，计数不确证）；${seg}${anchor}`
  }
  if (rc.resolution === 'noisy') {
    return `${rc.filename}（${d}）：调用 ${called}，可分辨峰 ${pk} → 峰数多于调用（疑似滑移肩峰），计数不可作证据；${seg}${anchor}`
  }
  const arrow = (rc.peak_count != null && rc.called_count != null)
    ? (rc.peak_count === rc.called_count
        ? '峰图与调用一致'
        : `峰图与调用有出入（差 ${Math.abs(rc.peak_count - rc.called_count)}），标记矛盾`)
    : '峰数不可计'
  return `${rc.filename}（${d}）：调用 ${called}，可分辨峰 ${pk} → ${arrow}；${seg}`
}
</script>

<template>
  <div class="conclusion-card cds-card" v-if="homopolymers.length">
    <h4 class="section-title">poly 同聚物 / 重复结构<span class="map-sub">（仅显示长度 ≥ </span>
      <select class="poly-thresh" :value="polyMinLen" @change="onPolyThresh"
              title="低于该长度的短同聚物/微卫星重复默认隐藏；调低可查看观察级结构">
        <option value="8">8</option>
        <option value="12">12</option>
        <option value="20">20</option>
        <option value="30">30</option>
        <option value="50">50</option>
      </select>
      <span class="map-sub"> bp 的结构；indel 落入时给出参考/测得重复数）</span></h4>
    <div v-for="h in polyEntries" :key="h.start" class="cds-row">
      <span class="cds-dot" :class="h.count_reliable ? 'pass' : 'mid'"></span>
      <div class="cds-main">
        <p class="cds-name">
          {{ polyName(h) }}
          <span class="cds-coord">{{ h.start }}-{{ h.end }}（参考 {{ h.ref_repeat_count }} {{ polyUnitLabel(h) }}）</span>
          <span class="cds-cov" :class="h.count_reliable ? 'full' : 'partial'">
            {{ runVerdictLabel(h) }}
          </span>
        </p>
        <!-- 主判读：以解读的峰图数据开头 -->
        <p class="cds-verdict">
          <template v-if="h.peak_measured != null">
            <template v-if="(h.peak_missing ?? 0) > 0">缺失 {{ h.peak_missing }} 个 {{ h.base }}：实测 {{ h.peak_measured }} {{ polyUnitLabel(h) }}，参考 {{ h.ref_repeat_count }} {{ polyUnitLabel(h) }}</template>
            <template v-else-if="(h.peak_inserted ?? 0) > 0">插入 {{ h.peak_inserted }} 个 {{ h.base }}：实测 {{ h.peak_measured }} {{ polyUnitLabel(h) }}，参考 {{ h.ref_repeat_count }} {{ polyUnitLabel(h) }}</template>
            <template v-else>poly({{ h.base }}) 碱基类型完整：实测 {{ h.peak_measured }} {{ polyUnitLabel(h) }}，参考 {{ h.ref_repeat_count }} {{ polyUnitLabel(h) }}</template>
          </template>
          <template v-else-if="h.read_counts?.length">峰图计数不可用，以宽度法估计为准</template>
          <template v-else>重复结构标注（该结构不做峰图计数）</template>
          <span v-if="polyEstNote(h)" class="poly-read-detail">（{{ polyEstNote(h) }}）</span>
        </p>
        <!-- 引物覆盖情况 -->
        <p class="cds-verdict" v-if="h.read_counts?.length">
          引物覆盖：<span class="poly-read-detail">{{ h.read_counts.map(readCountLabel).join('；') }}</span>
        </p>
        <!-- 逐引物判读 -->
        <p class="cds-detail" v-for="rc in h.read_counts ?? []" :key="rc.filename">
          <span>↳ {{ readVerdictLine(rc) }}</span>
        </p>
        <p class="cds-detail" v-if="h.read_counts?.length && !h.read_counts.some((rc) => rc.coverage === 'full')">
          <span>↳ 无完整覆盖的 read：主判读由各覆盖段峰图合成（缺失取各段最大值）</span>
        </p>
        <!-- 判定依据（Step B）：哪些 read 投的票、锚定状态；联合覆盖时标注 -->
        <p class="cds-detail" v-if="h.verdict_votes?.length && h.run_verdict === 'accepted'">
          <span>↳ 判定依据：{{ verdictBasis(h) }}</span>
        </p>
        <p class="cds-detail" v-if="h.run_verdict === 'contradictory'">
          <span>↳ 判定依据：可分辨 read 计数互不一致（{{ voteCounts(h) }}），需人工核对峰图或换引物复测</span>
        </p>
        <p class="cds-detail" v-if="h.run_covered != null && h.run_covered < h.ref_repeat_count">
          <span>↳ 各 read 合并覆盖该结构 {{ h.run_covered }}/{{ h.ref_repeat_count }} 个位置，未覆盖段无峰图证据</span>
        </p>
      </div>
    </div>
    <p class="cds-detail" v-if="polyHiddenCount > 0">
      <span>另有 {{ polyHiddenCount }} 条长度 &lt; {{ polyMinLen }}bp 的短同聚物/微卫星重复已默认隐藏（一般结构，无需核对重复数），调低上方阈值可查看</span>
    </p>
  </div>
</template>

<style scoped>
/* poly 卡样式：随模板从 SequencingPanel.vue 迁入
   （Vue scoped 样式不会穿透到子组件内部节点，父级规则管不到这里）。
   卡片底座 .conclusion-card/.cds-* 与 ConclusionCards.vue 保持同款 */
.conclusion-card {
  background: #FDF3F3; border: 1px solid #F2C6C6; border-radius: 10px; padding: 1rem 1.25rem;
}
.cds-card { margin-top: 0.75rem; }
.cds-row { display: flex; gap: 0.6rem; padding: 0.5rem 0; border-top: 1px dashed #E8E8E8; }
.cds-dot { width: 10px; height: 10px; border-radius: 50%; margin-top: 5px; flex: none; }
.cds-dot.pass { background: #2E9E44; }
.cds-dot.fail { background: #C0392B; }
.cds-dot.na { background: #BBB; }
.cds-dot.mid { background: #E6A700; }
.cds-name { font-weight: 600; margin: 0; }
.cds-coord { font-weight: 400; color: #888; font-size: 0.78rem; font-family: Consolas, monospace; }
.cds-cov { font-size: 0.72rem; font-weight: 400; padding: 1px 8px; border-radius: 10px; margin-left: 8px; vertical-align: 1px; }
.cds-cov.full { background: #E5F5E9; color: #227A36; }
.cds-cov.partial { background: #FCF3DC; color: #9A6D00; }
.cds-verdict { margin: 0.2rem 0 0; font-size: 0.86rem; }
.cds-detail { margin: 0.3rem 0 0; font-size: 0.78rem; color: #A03A2E; display: flex; flex-wrap: wrap; gap: 0.35rem 0.9rem; }
.poly-thresh {
  width: auto; min-width: 0; padding: 0 2px; font-size: 0.75rem; font-weight: 400;
  border: 1px solid #CBD5E1; border-radius: 4px; background: #fff; color: #334155;
  vertical-align: middle;
}
.poly-read-detail { color: #777; }
.section-title { font-size: 0.95rem; margin: 0 0 0.5rem; }
.map-sub { font-size: 0.75rem; color: #999; font-weight: 400; }
</style>
