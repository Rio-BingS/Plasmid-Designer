<script setup lang="ts">
/**
 * Sanger 测序全自动分析面板
 * 参考序列文件（.gb/.fasta/.dna）与 .ab1 放同一文件夹一起导入 →
 * 一键分析 → 总览结论 / 覆盖率 / 突变表 / 峰图 / 共识序列导出
 */
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import {
  analyzeSequencingFiles, getReadTrace, exportConsensus, formatApiError,
  type SequencingAnalysis, type SequencingVariant, type ReadTrace
} from '@/api'

const props = defineProps<{
  /** 深链预填的参考序列文件（如从设计结果页跳转时自动带入） */
  initialReference?: File | null
  /** 外部注入的已完成分析（历史回看），注入后直接展示结果 */
  preset?: SequencingAnalysis | null
}>()

const emit = defineEmits<{
  (e: 'analyzed', analysis: SequencingAnalysis): void
}>()

// ==================== 文件导入与分类 ====================
const REFERENCE_EXTS = ['gb', 'gbk', 'genbank', 'fasta', 'fa', 'fna', 'dna']

const referenceFile = ref<File | null>(null)
const autoFilledRef = ref(false)     // 当前参考是否为深链自动带入（用户显式选择可随时顶掉）
const replacedAutoName = ref('')     // 被用户文件替换掉的深链参考名（提示用）
const reads = ref<File[]>([])
const ignoredNames = ref<string[]>([])   // 既非参考也非 .ab1 的文件
const conflictNames = ref<string[]>([])  // 多余的参考文件
const fileError = ref('')

function fileExt(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1).toLowerCase() : ''
}

function addFiles(list: File[] | FileList | null | undefined) {
  if (!list) return
  ignoredNames.value = []
  conflictNames.value = []
  replacedAutoName.value = ''
  fileError.value = ''
  for (const f of Array.from(list)) {
    const ext = fileExt(f.name)
    if (ext === 'ab1') {
      reads.value.push(f)
    } else if (REFERENCE_EXTS.includes(ext)) {
      if (referenceFile.value && !autoFilledRef.value) {
        conflictNames.value.push(f.name)
      } else {
        // 深链自动带入的参考让位给用户文件里的参考（显式操作优先，无需手动清除）
        if (autoFilledRef.value) replacedAutoName.value = referenceFile.value?.name || ''
        referenceFile.value = f
        autoFilledRef.value = false
      }
    } else {
      ignoredNames.value.push(f.name)
    }
  }
}

/** 深链进入时自动带入；深链消失（如点导航回普通 /sequencing）时清掉自动带入的，手动选择的不受影响 */
watch(() => props.initialReference, (f) => {
  if (f) {
    referenceFile.value = f
    autoFilledRef.value = true
  } else if (autoFilledRef.value) {
    referenceFile.value = null
    autoFilledRef.value = false
  }
}, { immediate: true })

// ==================== 拖拽导入（支持整个文件夹） ====================
const dragOver = ref(false)

interface FsEntry {
  isFile: boolean
  isDirectory: boolean
  file: (cb: (f: File) => void, err?: (e: unknown) => void) => void
  createReader: () => { readEntries: (cb: (entries: FsEntry[]) => void, err?: (e: unknown) => void) => void }
}

function onDrop(e: DragEvent) {
  dragOver.value = false
  const dt = e.dataTransfer
  if (!dt) return
  // webkitGetAsEntry 必须在事件处理同步阶段调用，先收集再异步遍历
  const entries: FsEntry[] = []
  const plainFiles: File[] = []
  for (const item of Array.from(dt.items || [])) {
    const entry = (item as unknown as { webkitGetAsEntry?: () => FsEntry | null }).webkitGetAsEntry?.()
    if (entry) entries.push(entry)
    else {
      const f = item.getAsFile()
      if (f) plainFiles.push(f)
    }
  }
  if (entries.length) {
    walkEntries(entries).then((files) => addFiles(files))
  } else {
    addFiles(dt.files)
  }
}

async function walkEntries(entries: FsEntry[]): Promise<File[]> {
  const out: File[] = []
  async function walk(entry: FsEntry) {
    if (entry.isFile) {
      const f = await new Promise<File | null>((res) => entry.file(res, () => res(null)))
      if (f) out.push(f)
    } else if (entry.isDirectory) {
      const reader = entry.createReader()
      let batch: FsEntry[] = []
      do {
        batch = await new Promise<FsEntry[]>((res) => reader.readEntries(res, () => res([])))
        for (const child of batch) await walk(child)
      } while (batch.length)
    }
  }
  for (const e of entries) await walk(e)
  return out
}

function onFilePick(e: Event) {
  addFiles((e.target as HTMLInputElement).files)
  ;(e.target as HTMLInputElement).value = ''
}
function onFolderPick(e: Event) {
  addFiles((e.target as HTMLInputElement).files)
  ;(e.target as HTMLInputElement).value = ''
}
function removeRead(f: File) {
  const i = reads.value.indexOf(f)
  if (i >= 0) reads.value.splice(i, 1)
}
function clearReads() { reads.value = [] }
function clearReference() { referenceFile.value = null; autoFilledRef.value = false }

// ==================== 分析 ====================
const minQ = ref(20)
const allowDecompose = ref(true)
const analyzing = ref(false)
const analysis = ref<SequencingAnalysis | null>(null)
const errorMsg = ref('')

/** 阈值合法范围 0-60（与后端校验一致），失焦时就近钳制避免 422 */
function normalizeMinQ() {
  if (!Number.isFinite(minQ.value)) { minQ.value = 20; return }
  minQ.value = Math.max(0, Math.min(60, Math.round(minQ.value)))
}

const canAnalyze = computed(() => !!referenceFile.value && reads.value.length > 0)

async function runAnalysis() {
  if (!referenceFile.value || !reads.value.length) return
  analyzing.value = true
  errorMsg.value = ''
  analysis.value = null
  try {
    analysis.value = await analyzeSequencingFiles(
      referenceFile.value, reads.value, minQ.value, allowDecompose.value
    )
    resetSeqViz()
    if (analysis.value.reads.length) {
      visibleReads.value = [analysis.value.reads[0].index]
      selectedReadIdx.value = 0
      loadSeqTrace(analysis.value.reads[0].index)
    }
    emit('analyzed', analysis.value)
  } catch (e: any) {
    errorMsg.value = formatApiError(e, '分析失败')
  } finally {
    analyzing.value = false
  }
}

// ==================== 覆盖率条带 ====================
const coverageSegments = computed(() => {
  const a = analysis.value
  if (!a) return []
  const L = a.reference_length
  return a.coverage_ranges.map(([s, e]) => ({ left: ((s - 1) / L) * 100, width: ((e - s + 1) / L) * 100 }))
})

// ==================== 比对峰图（SnapGene 式融合视图） ====================
// 参考碱基行 + 各 read 碱基行 + 四通道峰图画在同一个参考坐标轴上：差异列的
// 证据（参考碱基/read 碱基/Q/峰形/次级峰占比）一屏看完，不再在比对网格与
// 独立峰图面板之间来回跳。横轴 = 参考坐标（插入列在左右两列间插缝），
// Ctrl+滚轮缩放。注意：必须在下方 preset 的 immediate watch 之前声明
const seqBox = ref<HTMLElement | null>(null)
const seqCanvas = ref<HTMLCanvasElement | null>(null)
const seqColW = ref(12)                 // 每个参考 bp 的像素宽（缩放）
const visibleReads = ref<number[]>([])  // 显示中的 read index（按行序）
const traceCache = ref<Record<number, ReadTrace | null>>({})
const seqTraceLoading = ref(false)
const selRefPos = ref<number | null>(null)   // 点选列（参考坐标）
const flashRefPos = ref<number | null>(null) // 跳转高亮列（短暂）
const seqInfo = ref('')
const jumpInput = ref('')
let seqScrollX = 0
let seqRaf = 0
let flashTimer: ReturnType<typeof setTimeout> | undefined

const SEQ_TRACE_H = 120  // 峰图条带高（SnapGene 式大峰，塞满带内空间）
const SEQ_ROW_H = 16     // 碱基字母行高
const OV_ARROW_H = 18    // 覆盖简图每条引物箭头高
const OV_LANE_GAP = 4    // 覆盖简图泳道间隔
/** 覆盖简图布局（简图固定整参考宽度，不随横向滚动移动；seqWrapH 与 drawSeq 共用一套数字） */
function ovLayoutFor(laneCount: number) {
  const arrowBlock = 6 + laneCount * (OV_ARROW_H + OV_LANE_GAP)
  return { arrowBlock, ovCovY: arrowBlock + 6, ovH: arrowBlock + 6 + 8 }
}
// 泳道分配：按落点起点贪心装箱，同泳道内 read 互不重叠（与匹配简图同思路）
const ovLanes = computed(() => {
  const reads = analysis.value?.reads ?? []
  const lanes: number[][] = []
  const laneOf: number[] = reads.map(() => -1)
  const order = reads.map((_, i) => i).sort((x, y) => reads[x].ref_start - reads[y].ref_start)
  for (const i of order) {
    const r = reads[i]
    if (r.ref_end <= 0) continue   // 无对齐 read 没有落点，不上图
    let placed = false
    for (let l = 0; l < lanes.length; l++) {
      if (lanes[l].every((j) => reads[j].ref_end < r.ref_start)) { lanes[l].push(i); laneOf[i] = l; placed = true; break }
    }
    if (!placed) { lanes.push([i]); laneOf[i] = lanes.length - 1 }
  }
  return { lanes, laneOf }
})
const ovLaneCount = computed(() => Math.max(1, ovLanes.value.lanes.length))
const seqWrapH = computed(() => {
  // 覆盖简图带 + 刻度尺14 + 参考行16 + 间隔4 + 各显示 read 紧凑字母行 +
  // 单条峰图带（选中 read）+ 底部8；末尾 18px 是水平滚动条补偿：
  // wrap.clientHeight 不含滚动条，少算会把带底位号裁掉
  const { ovH } = ovLayoutFor(ovLaneCount.value)
  const nVis = visibleReads.value.length
  return ovH + 14 + 16 + 4 + nVis * SEQ_ROW_H + 6
    + (nVis > 0 ? SEQ_TRACE_H + 14 : 26) + 8 + 18
})
const seqSpacerW = computed(() => (analysis.value?.reference_length ?? 0) * seqColW.value)

// 当前峰图带展示哪条 read（简图/字母行/复选框点选都汇聚到这里）
const selectedReadIdx = ref<number | null>(null)

interface SeqCol {
  ref: string
  read: string
  q: number
  mm: boolean
  ins: boolean
  refPos: number
  origIdx: number   // 原始电泳顺序的 read 碱基下标（0-based；read 缺口列 = -1）
  xu: number        // 横轴坐标（参考 bp 单位；插入列在缝内插值）
  xEnd: number
}

// 逐 read 列模型缓存：alignment_view 是参考方向的逐列对齐（反向 read 已折算）
const seqColCache: Record<number, SeqCol[] | null> = {}

function buildSeqCols(read: SequencingAnalysis['reads'][number]): SeqCol[] | null {
  const av = read.alignment_view
  if (!av || !av.ref_aligned) return null
  const L = read.trimmed_length
  const cols: SeqCol[] = []
  let refPos = av.ref_start
  let qi = -1
  for (let i = 0; i < av.ref_aligned.length; i++) {
    const rb = av.ref_aligned[i]
    const qb = av.read_aligned[i]
    if (qb !== '-') qi++
    // 反向 read 的 query 是 revcomp：原始下标 = L-1-query 下标（与后端镜像同式）
    const origIdx = qb === '-' ? -1 : (read.direction === '-' ? L - 1 - qi : qi)
    cols.push({
      ref: rb, read: qb, q: av.q_aligned?.[i] ?? 0,
      mm: rb !== '-' && qb !== '-' && rb !== qb,
      ins: rb === '-' && qb !== '-',
      refPos: rb !== '-' ? refPos : 0,
      origIdx, xu: 0, xEnd: 0,
    })
    if (rb !== '-') refPos++
  }
  // 横轴坐标：常规列落在其参考 bp 中心；连续插入列在左右两列之间等分插缝
  let i = 0
  let prevXu = -1
  while (i < cols.length) {
    if (!cols[i].ins) {
      cols[i].xu = cols[i].refPos - 0.5
      prevXu = cols[i].xu
      i++
      continue
    }
    let j = i
    while (j < cols.length && cols[j].ins) j++
    const nextXu = j < cols.length ? cols[j].refPos - 0.5 : prevXu + 1
    const n = j - i
    for (let m = i; m < j; m++) cols[m].xu = prevXu + (nextXu - prevXu) * ((m - i + 1) / (n + 1))
    i = j
  }
  for (let k = 0; k < cols.length; k++) {
    cols[k].xEnd = k + 1 < cols.length ? cols[k + 1].xu : cols[k].xu + 1
  }
  return cols
}

function seqColsFor(readIndex: number): SeqCol[] | null {
  if (!(readIndex in seqColCache)) {
    const r = analysis.value?.reads[readIndex]
    seqColCache[readIndex] = r ? buildSeqCols(r) : null
  }
  return seqColCache[readIndex]
}

function resetSeqViz() {
  visibleReads.value = []
  traceCache.value = {}
  selRefPos.value = null
  flashRefPos.value = null
  selectedReadIdx.value = null
  seqInfo.value = ''
  jumpInput.value = ''
  seqColW.value = 12
  seqScrollX = 0
  for (const k of Object.keys(seqColCache)) delete seqColCache[Number(k)]
}

/** 覆盖缺口摘要（取最长 3 段展示） */
const gapSummary = computed(() => {
  const gs = analysis.value?.coverage_gaps ?? []
  const parts = gs.slice(0, 3).map((g) => `${g.start}-${g.end}（${g.length}bp）`)
  return parts.join('、') + (gs.length > 3 ? ' 等' : '')
})

// 低置信差异（单 read 支持 / 低 Q / 疑似混合峰）默认折叠：不删除、不影响
// 共识与 CDS 结论，只是收起避免刷屏；点开展开供人工核对峰图
const showLowConf = ref(false)
const showMixedDetail = ref(false)
const lowConfVariants = computed(() =>
  (analysis.value?.variants ?? []).filter((v) => v.confidence === 'low'))
const shownVariants = computed(() => {
  const all = analysis.value?.variants ?? []
  return showLowConf.value ? all : all.filter((v) => v.confidence !== 'low')
})
// 结论文本按组折叠：低置信行（后端标注"低置信（/低置信度（"）连同紧随的
// "↳ 破坏/新增酶切位点"子注释行一起收起；poly 判读等独立行不受牵连。
// 逐 read 双峰位点范围行（"↳ …双峰 N 处，位于 read …"）单独一组默认收起，
// 展开后可核对位点是否落在 read 首尾不可信区
const conclusionParts = computed(() => {
  const lines = (analysis.value?.conclusion ?? '').split('\n')
  const isLowHead = (l: string) => l.includes('低置信（') || l.includes('低置信度（')
  const isSubNote = (l: string) => l.trimStart().startsWith('↳') && l.includes('酶切位点')
  const isMixedDetail = (l: string) => l.trimStart().startsWith('↳') && l.includes('双峰')
  const main: string[] = []
  const low: string[] = []
  const mixed: string[] = []
  let prevFolded = false
  for (const l of lines) {
    if (isMixedDetail(l)) {
      mixed.push(l)
      prevFolded = false
      continue
    }
    const folded: boolean = isLowHead(l) || (isSubNote(l) && prevFolded)
    ;(folded ? low : main).push(l)
    prevFolded = folded
  }
  return { main: main.join('\n'), low, mixed }
})

/** CDS 结论卡的覆盖标签 */
function cdsCoverageLabel(c: { coverage_status: string; covered_percent: number }): string {
  if (c.coverage_status === 'uncovered') return '未覆盖'
  if (c.coverage_status === 'full') return '完整覆盖'
  return `覆盖 ${c.covered_percent}%`
}

// ==================== poly 同聚物 / 重复结构卡片 ====================
type Hp = NonNullable<SequencingAnalysis['homopolymers']>[number]

// 显示阈值：默认 20bp（=后端 HOMOPOLYMER_MIN），只显示需要核对重复数的
// poly 功能结构；8-19bp 观察级同聚物与二/三核苷酸重复默认隐藏，可调低查看
const polyMinLen = ref(20)
function onPolyThresh(e: Event) {
  polyMinLen.value = Number((e.target as HTMLSelectElement).value)
}
const polyEntries = computed(() =>
  (analysis.value?.homopolymers ?? [])
    .filter((h) => (h.length ?? h.end - h.start + 1) >= polyMinLen.value))
const polyHiddenCount = computed(
  () => (analysis.value?.homopolymers ?? []).length - polyEntries.value.length)

/** 结构名：period=1 用 poly(A)；period>1 是二/三核苷酸重复，按 (AT)×4 标注，
 *  避免把"参考 4 个单元"误读成 4bp 的 polyA */
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
/** 信号反卷积的长度估计：method=peaks 时估计就是可分辨峰数，与主判读
 *  重复，不重复展示；宽度法/二阶导数是独立口径，保留 */
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

/** 逐引物判读行：调用数 vs 可分辨峰 → 是否出入；覆盖情况与证据边界 */
function readVerdictLine(rc: NonNullable<Hp['read_counts']>[number]): string {
  const d = rc.direction === '-' ? '反向' : '正向'
  const called = rc.called_count ?? '?'
  const pk = rc.peak_count ?? '?'
  const arrow = (rc.peak_count != null && rc.called_count != null)
    ? (rc.peak_count === rc.called_count
        ? '峰图与调用一致'
        : `峰图与调用有出入（差 ${Math.abs(rc.peak_count - rc.called_count)}），标记矛盾`)
    : '峰数不可计'
  const seg = rc.coverage === 'partial' && rc.covered_span
    ? `仅覆盖该结构 ${rc.covered_span[0]}-${rc.covered_span[1]}，未完整跨过（read 在结构内截断/起始）——覆盖段峰图已参与核对`
    : '完整跨过该结构'
  return `${rc.filename}（${d}）：调用 ${called}，可分辨峰 ${pk} → ${arrow}；${seg}`
}

// SO 标准后果词表 → 中文标签与影响等级（VEP/snpEff 同款分级）
const SO_LABELS: Record<string, string> = {
  stop_gained: '无义突变',
  stop_lost: '终止丢失',
  start_lost: '起始丢失',
  frameshift_variant: '移码',
  inframe_insertion: '框内插入',
  inframe_deletion: '框内缺失',
  missense_variant: '错义',
  synonymous_variant: '同义',
}
const SO_HIGH = new Set(['stop_gained', 'stop_lost', 'start_lost', 'frameshift_variant'])
const SO_MID = new Set(['inframe_insertion', 'inframe_deletion', 'missense_variant'])
function soLevel(t: string): string {
  if (SO_HIGH.has(t)) return 'high'
  if (SO_MID.has(t)) return 'mid'
  return 'low'
}

/** 置信度徽章的悬停说明：附峰级证据（突变峰占比 / 信噪比 / 插入峰强度比） */
function confTitle(v: { confidence?: string; corroborated_by_basecall?: boolean; peak_evidence?: { mutant_pct?: number | null; snr?: number | null; insertion_peak_ratio?: number | null } }): string {
  const ev = v.peak_evidence
  let peak = ''
  if (ev && ev.mutant_pct != null) {
    peak = `峰级证据：突变峰占比 ${ev.mutant_pct}%，信噪比 ${ev.snr ?? '—'}`
  } else if (ev && ev.insertion_peak_ratio != null) {
    peak = `峰级证据：插入峰强度为邻峰的 ${Math.round(ev.insertion_peak_ratio * 100)}%（≥60% 说明插入峰真实存在，Q 值在峰压缩区偏低属正常）`
  }
  if (v.corroborated_by_basecall) {
    peak += (peak ? '；' : '') + 'tracy 重 basecall 也报出此变异（同一测序信号的两个读出，仅供参考）'
  }
  if (v.confidence === 'low') return `低置信：疑似混合峰或 Q 值偏低${peak ? '；' + peak : ''}，务必人工核对峰图`
  if (v.confidence === 'medium') return `中置信：建议核对峰图${peak ? '；' + peak : ''}`
  return `高置信${peak ? '：' + peak : ''}`
}

// ==================== 匹配简图（SnapGene 风格线性图谱） ====================
// 上方 read 深红块状箭头（方向见箭头），中间刻度轴（绿段=已测覆盖、轴上红块=变异），
// 下方参考特征彩色块状箭头（类型配色与环形图谱一致，放不下的名字引线外置）。
const MAP_W = 1000        // viewBox 宽度
const MAP_GUTTER = 150    // 左侧文件名栏宽
const READS_TOP = 8       // read 区顶部
const READ_LANE_H = 36    // read 行高（read 是本图主角，行高/箭头约为特征的 2 倍）
const READ_H = 26         // read 箭头高度
const VAR_H = 14          // 轴上方变异标记带高度
const FEAT_LANE_H = 19    // 特征行高
const FEAT_H = 14         // 特征箭头高度
const LABEL_LANE_H = 16   // 特征外置标签行高

// 特征类型配色（与 PlasmidMap 环形图谱一致）
const FEATURE_COLORS: Record<string, string> = {
  promoter: '#E8656B', terminator: '#2FA98C', CDS: '#4E79C7', gene: '#4E79C7',
  origin: '#8FBF6B', rep_origin: '#8FBF6B', resistance: '#E8B93E', tag: '#B07FD8',
  MCS: '#E8923D', multiple_cloning_site: '#E8923D', regulatory: '#E8656B',
  other: '#9AA5B1'
}
function mapFeatureColor(type: string): string {
  return FEATURE_COLORS[type] || FEATURE_COLORS.other
}
function darkenColor(hex: string, amount: number): string {
  const n = parseInt(hex.slice(1), 16)
  const r = Math.round(((n >> 16) & 255) * (1 - amount))
  const g = Math.round(((n >> 8) & 255) * (1 - amount))
  const b = Math.round((n & 255) * (1 - amount))
  return `rgb(${r},${g},${b})`
}

type MapRead = SequencingAnalysis['reads'][number] & { diffs: number[]; lane: number; nameInside: boolean }

/** 该 read 全部差异（替换/插入/缺失）在参考上的位置 */
function readDiffPositions(r: SequencingAnalysis['reads'][number]): number[] {
  const av = r.alignment_view
  if (!av) return []
  const out = new Set<number>()
  let pos = av.ref_start
  for (let i = 0; i < av.ref_aligned.length; i++) {
    const rb = av.ref_aligned[i]
    const qb = av.read_aligned[i]
    if (rb !== '-') {
      if (qb === '-' || qb !== rb) out.add(pos)
      pos++
    } else if (qb !== '-') {
      out.add(Math.max(1, pos - 1))  // 插入列：记在左翼参考位置
    }
  }
  return [...out].sort((x, y) => x - y)
}

/** 首次适配分道：把互不重叠的区间堆进尽量少的行，返回每项道号 */
function assignLanes<T>(items: T[], startOf: (t: T) => number, endOf: (t: T) => number): number[] {
  const laneEnds: number[] = []
  return items.map((it) => {
    const s = startOf(it)
    const e = endOf(it)
    let li = laneEnds.findIndex((le) => s > le)
    if (li === -1) {
      laneEnds.push(e)
      li = laneEnds.length - 1
    } else {
      laneEnds[li] = e
    }
    return li
  })
}

/** 按落位排序的 read 行（附差异位置、堆叠道号；名字放得下画进箭头内） */
const mapRows = computed<MapRead[]>(() => {
  const a = analysis.value
  if (!a) return []
  const sorted = [...a.reads]
    .sort((x, y) => x.ref_start - y.ref_start || x.index - y.index)
    .map((r) => ({ ...r, diffs: readDiffPositions(r) }))
  const lanes = assignLanes(sorted, (r) => r.ref_start, (r) => r.ref_end)
  return sorted.map((r, i) => {
    const w = readWidth(r)
    const nameW = shortName(r.filename).length * 7 + 8
    const nameInside = w - arrowHeadW(w) - 10 >= nameW
    return { ...r, lane: lanes[i], nameInside }
  })
})

const mapScale = computed(() => {
  const a = analysis.value
  return a ? (MAP_W - 10 - MAP_GUTTER) / a.reference_length : 0
})

function mapX(pos: number): number {
  return MAP_GUTTER + (pos - 1) * mapScale.value
}

const readLaneCount = computed(() => mapRows.value.reduce((m, r) => Math.max(m, r.lane + 1), 0))
const axisY = computed(() => READS_TOP + readLaneCount.value * READ_LANE_H + VAR_H + 6)
const featsTop = computed(() => axisY.value + 24)

function readY(lane: number): number {
  return READS_TOP + lane * READ_LANE_H
}
function featY(lane: number): number {
  return featsTop.value + lane * FEAT_LANE_H
}
function labelY(lane: number): number {
  return featsTop.value + featLaneCount.value * FEAT_LANE_H + 14 + lane * LABEL_LANE_H
}

/** 箭头头部宽度（随条带宽度自适应，封顶限制扁条不被头部吃满） */
function arrowHeadW(w: number): number {
  return Math.min(26, Math.max(8, w * 0.2))
}

/** 块状箭头路径（forward 箭头朝右，否则朝左） */
function arrowPath(x1: number, x2: number, y: number, h: number, forward: boolean): string {
  const w = Math.max(2, x2 - x1)
  const aw = arrowHeadW(w)
  if (forward) {
    const bx = Math.max(x1, x2 - aw)
    return `M ${x1} ${y} L ${bx} ${y} L ${x2} ${y + h / 2} L ${bx} ${y + h} L ${x1} ${y + h} Z`
  }
  const bx = Math.min(x2, x1 + aw)
  return `M ${x2} ${y} L ${bx} ${y} L ${x1} ${y + h / 2} L ${bx} ${y + h} L ${x2} ${y + h} Z`
}

function readWidth(r: { ref_start: number; ref_end: number }): number {
  return Math.max(6, (r.ref_end - r.ref_start + 1) * mapScale.value)
}

interface MapFeat {
  name: string
  type: string
  start: number
  end: number
  strand: string
  x1: number
  x2: number
  lane: number
  labelInside: boolean
  labelX: number
  labelLane: number
}

/** 参考特征（彩色块状箭头；名字放不下时引线外置到下方标签道）。
 *  去重开关（默认关=按文件原样显示）：图谱文件里常见同名注释标了多条
 *  （如成对的 5 UTR / 邻接拼接的 miscellaneous），开启后同名且位置重叠
 *  或相邻（≤50bp）的合并为一条；方向不敏感（同一元件常被标在两条链上）。
 *  相距远的同名特征（如分布在两端的两个 3 UTR）不合并 */
const DEDUP_GAP_BP = 50
const dedupMapFeats = ref(false)
const mapFeats = computed<MapFeat[]>(() => {
  const a = analysis.value
  if (!a || !a.features?.length) return []
  let feats = [...a.features].sort((x, y) => x.start - y.start || x.end - y.end)
  if (dedupMapFeats.value) {
    const merged: typeof feats = []
    for (const f of feats) {
      // 按 start 升序扫描：f.start ≥ hit.start 恒成立，只需扩右端；
      // f.start − hit.end ≤ 阈值 同时覆盖重叠（负数）与邻接（0/1）情形
      const hit = merged.find((m) => m.name === f.name
        && f.start - m.end <= DEDUP_GAP_BP)
      if (hit) {
        hit.end = Math.max(hit.end, f.end)
      } else {
        merged.push({ ...f })
      }
    }
    feats = merged
  }
  const sorted = feats
  const lanes = assignLanes(sorted, (f) => f.start, (f) => f.end)
  const labelLaneEnds: number[] = []
  return sorted.map((f, i) => {
    const x1 = mapX(f.start)
    const x2 = x1 + Math.max(8, (f.end - f.start + 1) * mapScale.value)
    const labelW = f.name.length * 6.6 + 8
    const bodyW = x2 - x1 - arrowHeadW(x2 - x1) - 4
    const inside = f.name.length > 0 && labelW <= bodyW
    const cx = Math.min(MAP_W - 10 - labelW / 2, Math.max(MAP_GUTTER + labelW / 2, (x1 + x2) / 2))
    let labelLane = -1
    if (!inside) {
      let li = labelLaneEnds.findIndex((le) => cx - labelW / 2 > le + 8)
      if (li === -1) {
        labelLaneEnds.push(cx + labelW / 2)
        li = labelLaneEnds.length - 1
      } else {
        labelLaneEnds[li] = cx + labelW / 2
      }
      labelLane = li
    }
    return {
      name: f.name, type: f.type, start: f.start, end: f.end, strand: f.strand || '+',
      x1, x2, lane: lanes[i], labelInside: inside, labelX: cx, labelLane
    }
  })
})

/** 开启去重后实际合并掉的注释条数（0 = 图里没有满足合并条件的同名邻近注释） */
const dedupMergedCount = computed(() => {
  if (!dedupMapFeats.value || !analysis.value?.features?.length) return 0
  return analysis.value.features.length - mapFeats.value.length
})
const featLaneCount = computed(() => mapFeats.value.reduce((m, f) => Math.max(m, f.lane + 1), 0))
const labelLaneCount = computed(() =>
  mapFeats.value.reduce((m, f) => Math.max(m, f.labelInside ? 0 : f.labelLane + 1), 0))

const mapHeight = computed(() => Math.max(
  axisY.value + 28,
  featsTop.value + featLaneCount.value * FEAT_LANE_H + labelLaneCount.value * LABEL_LANE_H + 4
))

/** 参考条覆盖段（轴上叠加绿色已测段） */
const mapCovered = computed(() => analysis.value?.coverage_ranges ?? [])

/** 轴刻度（自适应步长，形如 SnapGene 的 2000/4000/6000） */
const mapTicks = computed(() => {
  const a = analysis.value
  if (!a) return []
  const L = a.reference_length
  const steps = [100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 50000]
  const step = steps.find((s) => L / s <= 9) ?? 100000
  const out: { pos: number; label: string }[] = []
  for (let p = step; p <= L; p += step) {
    out.push({ pos: p, label: p >= 10000 ? `${Math.round(p / 1000)}k` : String(p) })
  }
  return out
})

/** 图例里出现的特征类型（按出现顺序去重） */
const mapLegendTypes = computed(() => {
  const seen = new Set<string>()
  const out: { type: string; color: string }[] = []
  for (const f of mapFeats.value) {
    const key = FEATURE_COLORS[f.type] ? f.type : 'other'
    if (!seen.has(key)) {
      seen.add(key)
      out.push({ type: key === 'other' ? '其他' : key, color: FEATURE_COLORS[key] })
    }
  }
  return out
})

function shortName(name: string): string {
  return name.length > 18 ? name.slice(0, 17) + '…' : name
}

// ==================== 峰图数据加载 ====================

// 历史回看：注入已完成分析后直接展示（immediate 覆盖挂载时即带 preset 的场景）
watch(() => props.preset, (p) => {
  if (p) {
    analysis.value = p
    errorMsg.value = ''
    resetSeqViz()
    visibleReads.value = p.reads.length ? [0] : []
    if (p.reads.length) {
      selectedReadIdx.value = 0
      loadSeqTrace(0)
    }
  } else {
    // 退出历史回看：清空上次注入的分析状态，避免面板残留旧结果造成误读
    analysis.value = null
    errorMsg.value = ''
    resetSeqViz()
    showLowConf.value = false
    showMixedDetail.value = false
  }
}, { immediate: true })

const CHANNEL_COLORS: Record<string, string> = { A: '#2E9E44', T: '#D0342C', G: '#222222', C: '#2456C8' }

// 峰图按 read 缓存（多 read 堆叠时各自取用；TTL 清理后为 null，仅剩碱基行）
async function loadSeqTrace(ri: number): Promise<ReadTrace | null> {
  if (!analysis.value) return null
  if (traceCache.value[ri] !== undefined) return traceCache.value[ri]
  seqTraceLoading.value = true
  try {
    const t = await getReadTrace(analysis.value.analysis_id, ri)
    traceCache.value = { ...traceCache.value, [ri]: t ?? null }
    nextSeqDraw()
    return t ?? null
  } catch (e: any) {
    traceCache.value = { ...traceCache.value, [ri]: null }
    errorMsg.value = e.response?.data?.detail || '峰图加载失败'
    return null
  } finally {
    seqTraceLoading.value = false
  }
}

function isReadVisible(ri: number) {
  return visibleReads.value.includes(ri)
}

async function toggleRead(ri: number) {
  const i = visibleReads.value.indexOf(ri)
  if (i >= 0) {
    visibleReads.value.splice(i, 1)
    // 取消的是当前峰图 read → 回退到最后一条仍显示的
    if (selectedReadIdx.value === ri) {
      selectedReadIdx.value = visibleReads.value.length
        ? visibleReads.value[visibleReads.value.length - 1] : null
    }
    nextSeqDraw()
    return
  }
  visibleReads.value.push(ri)
  selectedReadIdx.value = ri
  await loadSeqTrace(ri)
  nextSeqDraw()
  // SnapGene 习惯：勾选一条 read 即看到它的落点——只有当它不覆盖当前窗口时才跳
  const read = analysis.value?.reads[ri]
  const wrap = seqBox.value
  if (read && wrap && wrap.clientWidth > 0) {
    const uL = seqScrollX / seqColW.value
    const uR = (seqScrollX + wrap.clientWidth) / seqColW.value
    if (read.ref_end < uL || read.ref_start > uR) scrollToRefPos(read.ref_start, true)
  }
}

function safeScrollTo(left: number, smooth: boolean) {
  const wrap = seqBox.value
  if (!wrap) return
  try {
    wrap.scrollTo({ left, behavior: smooth ? 'smooth' : 'auto' })
  } catch {
    wrap.scrollLeft = left   // jsdom 等环境无平滑滚动
  }
}

function scrollToRefPos(refPos: number, flash = false) {
  const wrap = seqBox.value
  if (wrap) {
    const target = Math.max(0, (refPos - 0.5) * seqColW.value - wrap.clientWidth / 2)
    // 立即定位（不用平滑滚动）：平滑滚动进行中缩放/连跳会取到中途 scrollLeft，
    // 造成视口漂移；目标列本身有 1.6s 黄色闪烁标识，无需动画引导
    safeScrollTo(target, false)
  }
  if (flash) {
    flashRefPos.value = refPos
    if (flashTimer) clearTimeout(flashTimer)
    flashTimer = setTimeout(() => {
      flashRefPos.value = null
      nextSeqDraw()
    }, 1600)
  }
  nextSeqDraw()
}

async function jumpToVariant(v: SequencingVariant) {
  if (!analysis.value) return
  const read = analysis.value.reads.find((r) => r.filename === (v.read || r.filename)) || analysis.value.reads[0]
  if (!read) return
  if (!isReadVisible(read.index)) {
    visibleReads.value.push(read.index)
    await nextTick()
  }
  selectedReadIdx.value = read.index   // 峰图条带切到支持该差异的 read
  await loadSeqTrace(read.index)
  selRefPos.value = v.ref_pos
  seqInfo.value = composeSeqInfo(v.ref_pos)
  seqBox.value?.scrollIntoView?.({ block: 'nearest' })
  scrollToRefPos(v.ref_pos, true)
}

function openReadInSeqviz(i: number) {
  const read = analysis.value?.reads[i]
  if (!read) return
  if (!isReadVisible(i)) {
    visibleReads.value.push(i)
    loadSeqTrace(i)
  }
  selectedReadIdx.value = i
  seqBox.value?.scrollIntoView?.({ block: 'nearest' })
  scrollToRefPos(read.ref_start)
}

/** 简图点选引物：峰图切到该 read，未显示的自动加入；
 *  zoom=true 时把主视图缩放到恰好容纳该 read 的覆盖区并居中（放大效果） */
async function selectRead(ri: number, zoom = false) {
  const read = analysis.value?.reads[ri]
  if (!read || read.ref_end <= 0) return
  const first = selectedReadIdx.value !== ri
  selectedReadIdx.value = ri
  if (!isReadVisible(ri)) {
    visibleReads.value.push(ri)
    loadSeqTrace(ri)
  }
  seqInfo.value = `已选中 ${shortName(read.filename)}（${read.direction === '-' ? '←' : '→'} ${read.ref_start}–${read.ref_end} · Q${read.mean_q}）${first ? '，峰图条带已切换到该引物' : ''}`
  const wrap = seqBox.value
  if (zoom && wrap && wrap.clientWidth > 0) {
    const span = Math.max(1, read.ref_end - read.ref_start + 1)
    const nu = Math.max(1, Math.min(28, Math.round(((wrap.clientWidth - 24) / span) * 100) / 100))
    if (nu !== seqColW.value) seqColW.value = nu
    scrollToRefPos((read.ref_start + read.ref_end) / 2, true)
  }
  nextSeqDraw()
}

/** 峰图带展示的 read：优先选中项，未选中时回退最后勾选的一条 */
function traceReadIdx(): number | null {
  if (selectedReadIdx.value != null && isReadVisible(selectedReadIdx.value)) return selectedReadIdx.value
  return visibleReads.value.length ? visibleReads.value[visibleReads.value.length - 1] : null
}

function jumpToRefPos() {
  const p = parseInt(jumpInput.value, 10)
  if (Number.isNaN(p) || !analysis.value) return
  if (p < 1 || p > analysis.value.reference_length) return
  selRefPos.value = p
  seqInfo.value = composeSeqInfo(p)
  scrollToRefPos(p, true)
}

function onSeqScroll() {
  seqScrollX = seqBox.value?.scrollLeft ?? 0
  nextSeqDraw()
}

function onSeqWheel(e: WheelEvent) {
  if (e.ctrlKey) {
    e.preventDefault()
    seqZoomAt(e.deltaY < 0 ? 1.2 : 1 / 1.2, e.offsetX)
  } else {
    // 纵向滚轮 → 横向浏览（峰图浏览器惯例）
    e.preventDefault()
    seqBox.value?.scrollBy?.({ left: e.deltaY })
    onSeqScroll()
  }
}

function seqZoomAt(f: number, anchorX?: number) {
  const old = seqColW.value
  const nu = Math.max(1, Math.min(28, Math.round(old * f * 100) / 100))
  if (nu === old) return
  if (anchorX != null) {
    const u = (seqScrollX + anchorX) / old
    seqColW.value = nu
    seqScrollX = Math.max(0, u * nu - anchorX)
    safeScrollTo(seqScrollX, false)
  } else {
    seqColW.value = nu
  }
  nextSeqDraw()
}

// 工具栏缩放以视口中心为锚——点放大/缩小不丢失正在看的位点
function seqZoom(f: number) {
  const wrap = seqBox.value
  seqZoomAt(f, wrap ? wrap.clientWidth / 2 : undefined)
}

function seqFit() {
  const wrap = seqBox.value
  if (!wrap || !analysis.value) return
  seqColW.value = Math.max(1, Math.min(28, Math.floor(wrap.clientWidth / analysis.value.reference_length)))
  safeScrollTo(0, false)
  nextSeqDraw()
}

function onSeqClick(e: MouseEvent) {
  const wrap = seqBox.value
  const a = analysis.value
  if (!wrap || !a) return
  const rect = wrap.getBoundingClientRect()
  const y = e.clientY - rect.top
  const { ovH } = ovLayoutFor(ovLaneCount.value)

  if (y < ovH) {
    // 覆盖简图（全景固定比例，不随滚动移动）：x 直接映射回参考位置
    const cw = wrap.clientWidth > 0 ? wrap.clientWidth : 1000
    const xLocal = e.clientX - rect.left
    const refPos = Math.min(Math.max(1, Math.ceil((xLocal / cw) * a.reference_length)), a.reference_length)
    const { lanes } = ovLanes.value
    const lane = Math.floor((y - 6) / (OV_ARROW_H + OV_LANE_GAP))
    // 命中该泳道内覆盖此位置的引物 → 选中并放大到其覆盖区；没命中 → 仅跳转
    let hit = -1
    if (lane >= 0 && lane < lanes.length) {
      let best = Infinity
      for (const i of lanes[lane]) {
        const r = a.reads[i]
        if (refPos < r.ref_start - 1 || refPos > r.ref_end + 1) continue
        const d = refPos < r.ref_start ? r.ref_start - refPos : refPos > r.ref_end ? refPos - r.ref_end : 0
        if (d < best) { best = d; hit = i }
      }
    }
    if (hit >= 0) {
      selectRead(hit, true)
    } else {
      selRefPos.value = refPos
      seqInfo.value = composeSeqInfo(refPos)
      scrollToRefPos(refPos, true)
    }
    return
  }

  // 主区：点在字母行上 → 峰图切到该 read；列点选证据逻辑不变
  const lettersTop = ovH + 14 + 2 + SEQ_ROW_H + 4
  const rowN = Math.floor((y - lettersTop) / SEQ_ROW_H)
  if (rowN >= 0 && rowN < visibleReads.value.length) {
    const ri = visibleReads.value[rowN]
    if (selectedReadIdx.value !== ri) {
      selectedReadIdx.value = ri
      nextSeqDraw()
    }
  }
  const x = e.clientX - rect.left + seqScrollX
  const refPos = Math.floor(x / seqColW.value) + 1
  if (refPos < 1 || refPos > a.reference_length) return
  selRefPos.value = refPos
  seqInfo.value = composeSeqInfo(refPos)
  nextSeqDraw()
}

/** 点选列的证据摘要：各可见 read 的碱基/Q/双峰占比 + 落在该位的差异注释 */
function composeSeqInfo(refPos: number): string {
  const a = analysis.value
  if (!a) return ''
  const parts: string[] = [`参考位置 ${refPos}`]
  for (const ri of visibleReads.value) {
    const cols = seqColsFor(ri)
    const read = a.reads[ri]
    if (!cols || !read) continue
    const hit = cols.find((c) => c.refPos === refPos && c.read !== '-')
    if (hit) {
      let s = `${shortName(read.filename)} ${hit.read}（Q${hit.q || '?'}`
      const md = (read.mixed_detail || []).find((d) => d.pos - 1 === hit.origIdx)
      // ratio 是次峰/主峰面积比，次要克隆占比 = r/(1+r)（与结论聚合口径一致）
      if (md) s += `，双峰：次峰 ${md.secondary_base} 占 ${Math.round((md.ratio / (1 + md.ratio)) * 100)}%`
      s += '）'
      parts.push(s)
    } else {
      const insHere = cols.some((c) => c.ins && Math.floor(c.xu) + 1 === refPos)
      parts.push(`${shortName(read.filename)} 未覆盖${insHere ? '（此处 read 有插入碱基）' : ''}`)
    }
  }
  const v = a.variants.find((x) => x.ref_pos === refPos)
  if (v) {
    const feats = (v.features || []).map((f) => f.name).join('、')
    parts.push(`差异：${v.ref_base}→${v.alt_base}（${feats || '非编码区'}${v.aa_change ? '，' + v.aa_change : ''}）`)
  }
  return parts.join(' · ')
}

// ==================== 融合视图绘制 ====================
function nextSeqDraw() {
  cancelAnimationFrame(seqRaf)
  seqRaf = requestAnimationFrame(drawSeq)
}

function seqX(xu: number): number {
  return xu * seqColW.value - seqScrollX
}

/** 单条 read 的峰图条带：逐列取该碱基的采样窗（peak apex 与邻峰中点），
 *  apex 对齐列中心——对任意采样密度/修剪偏移/反向 read 都成立 */
function drawSeqTrace(
  ctx: CanvasRenderingContext2D, t: ReadTrace, cols: SeqCol[],
  wins: { ci: number; lo: number; hi: number; apex: number }[],
  maxV: number, top: number, baseline: number,
) {
  const ch = t.channels as Record<string, number[]>
  for (const b of ['A', 'T', 'G', 'C']) {
    const arr = ch[b]
    if (!arr) continue
    ctx.beginPath()
    ctx.strokeStyle = CHANNEL_COLORS[b]
    ctx.lineWidth = 1.4
    let started = false
    for (const w of wins) {
      const c = cols[w.ci]
      const prevXu = w.ci > 0 ? cols[w.ci - 1].xu : c.xu - 1
      const nextXu = w.ci + 1 < cols.length ? cols[w.ci + 1].xu : c.xu + 1
      const xL = seqX((c.xu + prevXu) / 2)
      const xR = seqX((c.xu + nextXu) / 2)
      const xc = seqX(c.xu)
      const loN = Math.max(0, w.lo), hiN = Math.min(arr.length - 1, w.hi)
      for (let s = loN; s <= hiN; s++) {
        const v = arr[s]
        if (v == null) continue
        const x = s <= w.apex
          ? xL + (xc - xL) * (w.apex === w.lo ? 1 : (s - w.lo) / Math.max(1, w.apex - w.lo))
          : xc + (xR - xc) * (w.hi === w.apex ? 1 : (s - w.apex) / Math.max(1, w.hi - w.apex))
        const y = baseline - (baseline - top) * Math.min(1, v / maxV)
        if (!started) { ctx.moveTo(x, y); started = true } else ctx.lineTo(x, y)
      }
    }
    ctx.stroke()
  }
}

function drawSeq() {
  const canvas = seqCanvas.value
  const wrap = seqBox.value
  if (!canvas || !wrap) return
  const ctx = typeof canvas.getContext === 'function' ? canvas.getContext('2d') : null
  if (!ctx) return   // 无 2d 环境（happy-dom/jsdom 测试）跳过
  const a = analysis.value
  if (!a) return
  const dpr = window.devicePixelRatio || 1
  const w = wrap.clientWidth
  const h = wrap.clientHeight
  canvas.width = Math.max(1, Math.round(w * dpr))
  canvas.height = Math.max(1, Math.round(h * dpr))
  canvas.style.width = `${w}px`
  canvas.style.height = `${h}px`
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, h)

  const colW = seqColW.value
  const uLeft = seqScrollX / colW
  const uRight = (seqScrollX + w) / colW
  // 顶部覆盖简图（全景定位条）：固定整参考宽度、不随横向滚动移动，
  // 引物再多/放大多少倍都完整可见（SnapGene 图二式）
  const { ovCovY, ovH } = ovLayoutFor(ovLaneCount.value)
  const rulerH = ovH + 14
  const refRowY = rulerH + 2
  const refLen = a.reference_length
  const ovX = (bp: number) => (bp / refLen) * w
  const sel = selectedReadIdx.value

  // 0a) 覆盖并集绿条 + 轴线 + 变异刻度（低置信黄），与匹配简图同语义
  ctx.fillStyle = '#E2E2E2'
  ctx.fillRect(0, ovCovY + 2, w, 1)
  ctx.fillStyle = 'rgba(46,158,68,0.75)'
  for (const [s, e] of a.coverage_ranges ?? []) {
    ctx.fillRect(ovX(s - 1), ovCovY, Math.max(1, ovX(e) - ovX(s - 1)), 5)
  }
  for (const v of a.variants) {
    ctx.fillStyle = v.confidence === 'low' ? '#E6A700' : '#D0342C'
    ctx.fillRect(ovX(v.ref_pos - 0.5) - 1, ovCovY, 2, 5)
  }

  // 0b) 泳道箭头：当前视野覆盖到的引物着重，选中引物满色加边框并微微加高
  const UNTRUST = 20
  for (let i = 0; i < a.reads.length; i++) {
    const read = a.reads[i]
    const lane = ovLanes.value.laneOf[i]
    if (lane < 0) continue   // 无对齐 read 没有落点，不上图
    const isSel = sel === i
    const y = 6 + lane * (OV_ARROW_H + OV_LANE_GAP) + (isSel ? -1 : 0)
    const ah = OV_ARROW_H + (isSel ? 2 : 0)
    const x1 = ovX(read.ref_start - 1)
    const x2 = ovX(read.ref_end)
    const bw = Math.max(x2 - x1, 4)
    const inView = read.ref_end >= uLeft && read.ref_start <= uRight
    const vis = isReadVisible(i)
    const fwd = read.direction !== '-'
    const head = Math.min(14, Math.max(7, bw * 0.12))
    ctx.beginPath()
    if (fwd) {
      ctx.moveTo(x1, y); ctx.lineTo(x1 + bw - head, y); ctx.lineTo(x1 + bw, y + ah / 2)
      ctx.lineTo(x1 + bw - head, y + ah); ctx.lineTo(x1, y + ah)
    } else {
      ctx.moveTo(x1 + bw, y); ctx.lineTo(x1 + head, y); ctx.lineTo(x1, y + ah / 2)
      ctx.lineTo(x1 + head, y + ah); ctx.lineTo(x1 + bw, y + ah)
    }
    ctx.closePath()
    ctx.fillStyle = isSel ? '#B03A2E'
      : vis ? (inView ? 'rgba(176,58,46,0.85)' : 'rgba(176,58,46,0.5)')
      : (inView ? 'rgba(176,58,46,0.42)' : 'rgba(176,58,46,0.24)')
    ctx.fill()
    if (isSel) { ctx.strokeStyle = '#6E2018'; ctx.lineWidth = 1.5; ctx.stroke() }
    ctx.save()
    ctx.clip()
    // 两端不可信区（后端 END_MARGIN=20bp：信号爬升/下降段判读不可靠）
    ctx.fillStyle = 'rgba(255,255,255,0.45)'
    const eL = ovX(read.ref_start - 1 + UNTRUST)
    if (eL > x1) ctx.fillRect(x1, y, eL - x1, ah)
    const eR = ovX(read.ref_end - UNTRUST)
    if (eR < x1 + bw) ctx.fillRect(eR, y, x1 + bw - eR, ah)
    // 双峰位点（read 坐标 → 参考坐标），密集时自然连成"范围"
    ctx.fillStyle = '#F5A623'
    const ocols = seqColsFor(i)
    if (ocols) {
      for (const d of read.mixed_detail || []) {
        const c = ocols.find((cc) => cc.origIdx === d.pos - 1)
        if (!c || c.ref === '-') continue
        const x = ovX(c.xu)
        if (x >= x1 - 2 && x <= x1 + bw + 2) ctx.fillRect(x - 1, y, 2, ah)
      }
    }
    ctx.restore()
    // 名字居中印在箭头内（放得下才画）
    if (bw >= 44) {
      ctx.font = '9px Arial'
      ctx.textAlign = 'center'
      ctx.fillStyle = 'rgba(255,255,255,0.95)'
      ctx.fillText(shortName(read.filename), (x1 + x2) / 2, y + ah - 6, bw - head - 8)
    }
  }

  // 0c) 主视图当前窗口在简图上的投影（蓝框）
  const vx1 = ovX(Math.max(0, uLeft))
  const vx2 = ovX(Math.min(refLen, uRight))
  ctx.fillStyle = 'rgba(36,86,200,0.10)'
  ctx.fillRect(vx1, 2, Math.max(3, vx2 - vx1), ovH - 4)
  ctx.strokeStyle = 'rgba(36,86,200,0.5)'
  ctx.lineWidth = 1
  ctx.strokeRect(vx1 + 0.5, 2.5, Math.max(3, vx2 - vx1) - 1, ovH - 5)

  // 1) 刻度尺 + 纵向网格线（主视图坐标，从简图下沿开始）
  const steps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000]
  const step = steps.find((s) => s * colW >= 60) ?? 10000
  ctx.font = '9px Arial'
  ctx.textAlign = 'center'
  for (let p = Math.max(1, Math.ceil(uLeft / step) * step); p <= uRight; p += step) {
    const x = seqX(p - 0.5)
    ctx.fillStyle = '#999'
    ctx.fillText(p >= 10000 ? `${Math.round(p / 1000)}k` : String(p), x, rulerH - 4)
    ctx.strokeStyle = '#F0F0F0'
    ctx.beginPath()
    ctx.moveTo(x, rulerH)
    ctx.lineTo(x, h)
    ctx.stroke()
  }

  // 2) 参考碱基行（可见 read 的对齐参考并集；覆盖区浅绿底）
  const refCovered = new Map<number, string>()
  for (const ri of visibleReads.value) {
    const cols = seqColsFor(ri)
    if (!cols) continue
    for (const c of cols) {
      if (c.ref !== '-' && !refCovered.has(c.refPos)) refCovered.set(c.refPos, c.ref)
    }
  }
  ctx.fillStyle = 'rgba(46,158,68,0.10)'
  for (const [p] of refCovered) {
    const x = seqX(p - 1)
    if (x > -colW && x < w + colW) ctx.fillRect(x, refRowY, Math.max(colW, 2), SEQ_ROW_H)
  }
  if (colW >= 7) {
    ctx.font = '13px Consolas, monospace'
    ctx.textAlign = 'center'
    ctx.fillStyle = '#333'
    for (const [p, b] of refCovered) {
      const x = seqX(p - 0.5)
      if (x > -colW && x < w + colW) ctx.fillText(b, x, refRowY + 13)
    }
  }
  // 轴上变异刻度块（与匹配简图同语义；低置信差异用黄色区分）
  for (const v of a.variants) {
    const x = seqX(v.ref_pos - 0.5)
    if (x > -4 && x < w + 4) {
      ctx.fillStyle = v.confidence === 'low' ? '#E6A700' : '#D0342C'
      ctx.fillRect(x - 2, refRowY - 3, 4, 3)
    }
  }

  // 3) 各 read 紧凑字母行（不再逐 read 整块下排；峰图只画一条，见 4）
  const lettersTop = refRowY + SEQ_ROW_H + 4
  const lettersBottom = lettersTop + visibleReads.value.length * SEQ_ROW_H
  if (colW >= 8) {
    ctx.strokeStyle = '#F2F2F2'
    ctx.lineWidth = 1
    ctx.beginPath()
    for (let p = Math.ceil(uLeft); p <= Math.floor(uRight); p++) {
      const x = seqX(p - 0.5)
      ctx.moveTo(x, lettersTop)
      ctx.lineTo(x, lettersBottom)
    }
    ctx.stroke()
  }
  visibleReads.value.forEach((ri, rowN) => {
    const read = a.reads[ri]
    if (!read) return
    const rowY = lettersTop + rowN * SEQ_ROW_H
    const cols = seqColsFor(ri)
    const isSel = sel === ri
    // 行左侧固定名字芯片（滚动时也知道每行是谁）
    const chip = `${read.direction === '-' ? '←' : '→'} ${shortName(read.filename)}${cols ? '' : '（无对齐）'}`
    ctx.font = '10px Arial'
    ctx.textAlign = 'left'
    const chipW = Math.min(ctx.measureText(chip).width + 8, w - 4)
    ctx.fillStyle = 'rgba(255,255,255,0.92)'
    ctx.fillRect(0, rowY, chipW, SEQ_ROW_H - 1)
    ctx.fillStyle = !cols ? '#BBB' : isSel ? '#98422A' : '#B98A77'
    ctx.fillText(chip, 4, rowY + 11)

    if (!cols) return
    const mixedMap = new Map<number, { ratio: number; secondary_base: string }>()
    for (const d of read.mixed_detail || []) mixedMap.set(d.pos - 1, d)
    for (const c of cols) {
      const cx = seqX(c.xu)
      if (cx < -colW * 2 || cx > w + colW * 2) continue
      if (c.read === '-') {
        if (colW >= 7) { ctx.fillStyle = '#D8D8D8'; ctx.fillRect(cx - 1, rowY + 4, 2, 8) }
        continue
      }
      const md = mixedMap.get(c.origIdx)
      if (c.mm) {
        ctx.fillStyle = 'rgba(208,52,44,0.18)'
        ctx.fillRect(cx - colW / 2, rowY, Math.max(colW, 6), SEQ_ROW_H)
      }
      if (md) {
        ctx.fillStyle = 'rgba(230,126,34,0.16)'
        ctx.fillRect(cx - Math.max(colW / 2, 3), rowY, Math.max(colW, 6), SEQ_ROW_H)
      }
      if (colW >= 7) {
        ctx.textAlign = 'center'
        ctx.font = c.mm ? 'bold 13px Consolas, monospace' : '13px Consolas, monospace'
        ctx.fillStyle = c.q > 0 && c.q < 20 ? '#E67E22' : c.mm ? '#B03028' : isSel ? '#111' : '#666'
        ctx.fillText(c.read, cx, rowY + 13)
      }
    }
  })

  // 4) 单条峰图带（选中 read，未选中时回退最后勾选的）：直接交接在字母行下方
  const traceRi = traceReadIdx()
  const traceTop = lettersBottom + 6
  const baseline = traceTop + SEQ_TRACE_H - 14 // 峰图基线（带底留位号行）
  const bandBottom = baseline + 12
  if (traceRi == null) {
    ctx.fillStyle = '#C9C9C9'
    ctx.font = '11px Arial'
    ctx.textAlign = 'left'
    ctx.fillText('勾选上方引物或点击简图箭头查看峰图', 6, traceTop + 20)
  } else {
    const read = a.reads[traceRi]
    const cols = seqColsFor(traceRi)
    if (!read || !cols) {
      ctx.fillStyle = '#C9C9C9'
      ctx.font = '11px Arial'
      ctx.textAlign = 'left'
      ctx.fillText('该 read 无对齐数据，无法展示峰图', 6, traceTop + 20)
    } else {
      const mixedMap = new Map<number, { ratio: number; secondary_base: string }>()
      for (const d of read.mixed_detail || []) mixedMap.set(d.pos - 1, d)
      // 差异列浅红 / 双峰列橙底贯穿整条峰图带
      for (const c of cols) {
        const cx = seqX(c.xu)
        if (cx < -colW * 2 || cx > w + colW * 2 || c.read === '-') continue
        if (c.mm) {
          ctx.fillStyle = 'rgba(208,52,44,0.06)'
          ctx.fillRect(cx - colW / 2, traceTop, Math.max(colW, 6), bandBottom - traceTop)
        }
        if (mixedMap.get(c.origIdx)) {
          ctx.fillStyle = 'rgba(230,126,34,0.14)'
          ctx.fillRect(cx - Math.max(colW / 2, 3), traceTop, Math.max(colW, 6), bandBottom - traceTop)
        }
      }
      // 采样窗（可见列）：apex 与相邻峰中点围成本碱基区间
      const trace = traceCache.value[traceRi] ?? null
      const pk = trace?.peak_indices || []
      const trim = trace?.trim_start ?? 0
      const wins: { ci: number; lo: number; hi: number; apex: number }[] = []
      let maxV = 1
      for (let ci = 0; ci < cols.length; ci++) {
        const c = cols[ci]
        if (c.origIdx < 0) continue
        if (c.xu < uLeft - 2 || c.xu > uRight + 2) continue
        const iRaw = trim + c.origIdx
        const apex = pk[iRaw]
        if (apex == null || apex < 0) continue
        const prevRaw = iRaw > 0 ? pk[iRaw - 1] : null
        const nextRaw = iRaw + 1 < pk.length ? pk[iRaw + 1] : null
        const lo = prevRaw != null ? Math.round((prevRaw + apex) / 2) : Math.max(0, apex - 5)
        const hi = nextRaw != null ? Math.round((apex + nextRaw) / 2) : apex + 5
        wins.push({ ci, lo, hi, apex })
        const ch = trace!.channels as Record<string, number[]>
        for (const b of ['A', 'T', 'G', 'C']) {
          const arr = ch[b]
          if (!arr) continue
          for (let s = lo; s <= hi; s++) if (arr[s] > maxV) maxV = arr[s]
        }
      }
      if (trace && wins.length) {
        drawSeqTrace(ctx, trace, cols, wins, maxV, traceTop, baseline)
        // 混合位点标注：次峰碱基 + 次要克隆占比（r/(1+r)，与结论口径一致）
        if (colW >= 13) {
          ctx.font = '9px Arial'
          ctx.textAlign = 'center'
          for (const w2 of wins) {
            const c = cols[w2.ci]
            const md = mixedMap.get(c.origIdx)
            if (!md) continue
            ctx.fillStyle = '#E67E22'
            ctx.fillText(`${md.secondary_base}${Math.round((md.ratio / (1 + md.ratio)) * 100)}%`, seqX(c.xu), traceTop + 10)
          }
        }
      } else if (!trace && !seqTraceLoading.value) {
        ctx.fillStyle = '#C9C9C9'
        ctx.font = '11px Arial'
        ctx.textAlign = 'left'
        ctx.fillText('峰图不可用（加载失败或已过期，可重跑分析）', 6, traceTop + 20)
      }
      ctx.strokeStyle = '#444'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(0, baseline)
      ctx.lineTo(w, baseline)
      ctx.stroke()
      // 带内位号（基线下方，与顶部刻度尺互为参照）
      const pStep = [1, 2, 5, 10, 20, 50].find((s) => s * colW >= 46) ?? 100
      ctx.font = '9px Arial'
      ctx.textAlign = 'center'
      ctx.fillStyle = '#999'
      for (let p = Math.max(1, Math.ceil(uLeft / pStep) * pStep); p <= Math.min(uRight, refLen); p += pStep) {
        ctx.fillText(String(p), seqX(p - 0.5), bandBottom)
      }
      // 峰图归属标注（左上角小芯片）
      const tag = `峰图：${shortName(read.filename)}${trace ? '' : '（加载中…）'}`
      ctx.font = '10px Arial'
      ctx.textAlign = 'left'
      const tagW = Math.min(ctx.measureText(tag).width + 8, w - 4)
      ctx.fillStyle = 'rgba(255,255,255,0.92)'
      ctx.fillRect(0, traceTop, tagW, 13)
      ctx.fillStyle = '#98422A'
      ctx.fillText(tag, 4, traceTop + 10)
    }
  }

  // 5) 点选列竖线 + 跳转高亮（从简图下沿开始，避免盖住全景简图）
  if (selRefPos.value != null) {
    const x = seqX(selRefPos.value - 0.5)
    ctx.strokeStyle = '#2456C8'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(x, rulerH)
    ctx.lineTo(x, h)
    ctx.stroke()
  }
  if (flashRefPos.value != null) {
    const x = seqX(flashRefPos.value - 1)
    ctx.fillStyle = 'rgba(255, 220, 0, 0.28)'
    ctx.fillRect(x, rulerH, Math.max(colW, 6), h - rulerH)
  }
}

// 滚动/缩放/勾选/选中触发重绘；visibleReads 是 push/splice 原位变更，需 deep 才能触发
watch([visibleReads, seqColW, selectedReadIdx], nextSeqDraw, { deep: true })

function onSeqResize() { nextSeqDraw() }
onMounted(() => window.addEventListener('resize', onSeqResize))
onBeforeUnmount(() => {
  window.removeEventListener('resize', onSeqResize)
  if (flashTimer) clearTimeout(flashTimer)
})

// ==================== 共识序列 ====================
/** 分段渲染：与参考不同的位点高亮（cons_index 精确对应共识序列下标） */
const consensusSegments = computed(() => {
  const a = analysis.value
  if (!a) return []
  const seq = a.consensus.sequence
  const diffAt = new Set<number>()
  for (const d of a.consensus.diffs || []) {
    if (d.cons_index != null) diffAt.add(d.cons_index)
  }
  const out: { text: string; diff: boolean }[] = []
  for (let i = 0; i < seq.length; i++) {
    const d = diffAt.has(i)
    const last = out[out.length - 1]
    if (last && last.diff === d) last.text += seq[i]
    else out.push({ text: seq[i], diff: d })
  }
  return out
})

async function downloadConsensus(format: string) {
  const text = await exportConsensus(analysis.value!.analysis_id, format)
  const blob = new Blob([text], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `consensus.${format === 'genbank' ? 'gb' : 'fasta'}`
  link.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="seq-panel">
    <!-- 上传区 -->
    <div
      class="upload-area"
      :class="{ drag: dragOver }"
      @dragover.prevent="dragOver = true"
      @dragleave="dragOver = false"
      @drop.prevent="onDrop"
    >
      <p class="upload-title">🔬 Sanger 测序结果验证</p>
      <p class="upload-hint">
        把<b>参考序列文件</b>（图谱 .gb / .fasta / .dna）与 <b>.ab1 测序文件</b>放在同一个文件夹，
        拖入文件夹或选择文件，系统自动识别并完成解析、修剪、比对、拼接与突变注释
      </p>
      <div class="upload-btns">
        <label class="upload-btn">
          选择文件
          <input type="file" multiple accept=".ab1,.gb,.gbk,.genbank,.fasta,.fa,.fna,.dna" @change="onFilePick" hidden />
        </label>
        <label class="upload-btn secondary">
          选择文件夹
          <input type="file" multiple webkitdirectory @change="onFolderPick" hidden />
        </label>
      </div>

      <div v-if="referenceFile || reads.length" class="staged-files">
        <div v-if="referenceFile" class="ref-staged">
          <span class="stage-label">参考序列</span>
          <span class="file-chip ref">🧬 {{ referenceFile.name }}<button class="file-remove" title="移除参考" @click="clearReference">×</button></span>
        </div>
        <div v-if="reads.length" class="reads-staged">
          <span class="stage-label">测序文件 × {{ reads.length }}</span>
          <button class="clear-btn" title="清空全部 .ab1 测序文件" @click="clearReads">一键清除</button>
          <span v-for="f in reads" :key="f.name + f.size" class="file-chip">
            {{ f.name }} ({{ (f.size / 1024).toFixed(0) }}KB)
            <button class="file-remove" @click="removeRead(f)">×</button>
          </span>
        </div>
        <p v-if="conflictNames.length" class="stage-note">已忽略多余的参考文件：{{ conflictNames.join('、') }}（一次只能分析一个参考序列）</p>
        <p v-if="replacedAutoName" class="stage-note">已用文件里的参考替换深链带入的 {{ replacedAutoName }}</p>
        <p v-if="ignoredNames.length" class="stage-note">已忽略无关文件：{{ ignoredNames.slice(0, 5).join('、') }}{{ ignoredNames.length > 5 ? ' 等' : '' }}</p>
      </div>
      <p v-else class="stage-empty">尚未选择文件：需要 1 个参考序列文件 + 至少 1 个 .ab1</p>
      <p v-if="fileError" class="error-msg">{{ fileError }}</p>

      <details class="advanced">
        <summary>高级参数</summary>
        <label>末端修剪 Q 阈值（0-60，默认 20）<input type="number" v-model.number="minQ" min="0" max="60" @change="normalizeMinQ" /></label>
        <label><input type="checkbox" v-model="allowDecompose" /> 混合样品自动解卷积（需 tracy）</label>
      </details>
      <button class="analyze-btn" :disabled="!canAnalyze || analyzing" @click="runAnalysis">
        {{ analyzing ? '分析中…' : '开始自动分析' }}
      </button>
      <p v-if="!referenceFile && reads.length" class="hint-missing">
        还差参考序列文件（.gb / .fasta / .dna）
      </p>
      <p v-if="referenceFile && !reads.length" class="hint-missing">
        还差 .ab1 测序文件
      </p>
      <p v-if="errorMsg" class="error-msg">{{ errorMsg }}</p>
    </div>

    <template v-if="analysis">
      <!-- 结论总览 -->
      <div class="conclusion-card" :class="{ ok: analysis.variants.length === 0 }">
        <p class="conclusion-text">{{ conclusionParts.main }}</p>
        <button v-if="conclusionParts.low.length" class="lowconf-toggle" @click="showLowConf = !showLowConf">
          {{ showLowConf ? '▾ 收起低置信' : `▸ ${conclusionParts.low.filter((l) => !l.trimStart().startsWith('↳')).length} 处低置信已折叠（疑似测序噪声/混合峰，展开逐条核对）` }}
        </button>
        <p v-if="showLowConf && conclusionParts.low.length" class="conclusion-text lowconf-lines">{{ conclusionParts.low.join('\n') }}</p>
        <button v-if="conclusionParts.mixed.length" class="lowconf-toggle" @click="showMixedDetail = !showMixedDetail">
          {{ showMixedDetail ? '▾ 收起双峰位点范围' : `▸ ${conclusionParts.mixed.length} 条 read 的双峰位点范围已折叠（read 坐标，展开核对是否落在首尾）` }}
        </button>
        <p v-if="showMixedDetail && conclusionParts.mixed.length" class="conclusion-text lowconf-lines">{{ conclusionParts.mixed.join('\n') }}</p>
        <div class="conclusion-meta">
          <span>引擎: {{ analysis.engine }}</span>
          <span>共识覆盖率: {{ analysis.consensus.coverage_percent }}%</span>
          <span>差异: {{ analysis.variants.length }} 处</span>
        </div>
        <!-- 覆盖条带图 -->
        <div class="coverage-bar">
          <div
            v-for="(seg, i) in coverageSegments"
            :key="i"
            class="coverage-seg"
            :style="{ left: seg.left + '%', width: seg.width + '%' }"
          ></div>
        </div>
        <div class="coverage-labels"><span>1</span><span>{{ analysis.reference_length }} bp</span></div>
        <p class="coverage-gaps" v-if="analysis.coverage_gaps?.length">
          覆盖缺口 {{ analysis.coverage_gaps.length }} 段（按长度排序）：{{ gapSummary }} —— 建议从已测区边缘设计引物补测
        </p>
      </div>

      <!-- CDS 编码区测序结论：整段编码序列是否与参考一致 -->
      <div class="conclusion-card cds-card" v-if="analysis.cds_reports?.length">
        <h4 class="section-title">编码区（CDS）测序结论</h4>
        <div v-for="c in analysis.cds_reports" :key="c.name + c.start" class="cds-row">
          <span class="cds-dot" :class="c.protein_identical === null ? 'na' : (c.protein_identical ? 'pass' : 'fail')"></span>
          <div class="cds-main">
            <p class="cds-name">
              {{ c.name }}
              <span class="cds-coord">{{ c.start }}-{{ c.end }}（{{ c.strand === '-' ? '反向' : '正向' }}）</span>
              <span class="cds-cov" :class="c.coverage_status">{{ cdsCoverageLabel(c) }}</span>
              <span v-for="t in c.consequences ?? []" :key="t" class="cds-so" :class="soLevel(t)">{{ SO_LABELS[t] ?? t }}</span>
              <span v-if="c.pending_low_confidence" class="cds-so mid"
                    title="低置信变异（疑似测序噪声）未计入本结论，见结论末尾待复核说明">待复核 {{ c.pending_low_confidence }} 处</span>
            </p>
            <p class="cds-verdict">{{ c.verdict }}</p>
            <p class="cds-detail" v-if="c.protein_identical === false">
              <span v-if="c.premature_stop_aa">无义突变：翻译提前终止于第 {{ c.premature_stop_aa }} aa</span>
              <span v-if="c.frameshift_count">移码 {{ c.frameshift_count }} 处</span>
              <span v-if="c.ref_protein_length != null && c.alt_protein_length != null && c.ref_protein_length !== c.alt_protein_length">蛋白长度 {{ c.ref_protein_length }} → {{ c.alt_protein_length }} aa</span>
              <span v-if="c.aa_changes?.length">氨基酸替换（{{ c.aa_changes.slice(0, 5).join('、') }}{{ c.aa_changes.length > 5 ? '…' : '' }}）</span>
            </p>
          </div>
        </div>
      </div>

      <!-- poly 同聚物/重复结构：识别与重复数判读（阈值可调，默认只显示 ≥20bp 功能结构） -->
      <div class="conclusion-card cds-card" v-if="analysis.homopolymers?.length">
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
                {{ h.count_reliable ? '峰图与调用一致' : '峰图证据与调用不一致，建议核对峰图' }}
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
            <p class="cds-detail" v-if="h.run_covered != null && h.run_covered < h.ref_repeat_count">
              <span>↳ 各 read 合并覆盖该结构 {{ h.run_covered }}/{{ h.ref_repeat_count }} 个位置，未覆盖段无峰图证据</span>
            </p>
          </div>
        </div>
        <p class="cds-detail" v-if="polyHiddenCount > 0">
          <span>另有 {{ polyHiddenCount }} 条长度 &lt; {{ polyMinLen }}bp 的短同聚物/微卫星重复已默认隐藏（一般结构，无需核对重复数），调低上方阈值可查看</span>
        </p>
      </div>

      <!-- 匹配简图：SnapGene 风格线性图谱（read 箭头 / 刻度轴 / 参考特征） -->
      <div class="map-box" v-if="analysis.reads.length">
        <h4 class="section-title">匹配简图<span class="map-sub">（read 落位与参考特征一览；点击 read 或红块在比对峰图中查看）</span>
          <label class="map-dedup-toggle"
                 title="图谱文件里同名且位置重叠或相邻（≤50bp）的重复注释合并为一条显示（如成对的 5 UTR、邻接的 miscellaneous），方向不敏感；相距远的同名特征不受影响；默认按文件原样显示">
            <input type="checkbox" v-model="dedupMapFeats" /> 特征去重
          </label>
          <span v-if="dedupMergedCount > 0" class="map-sub">已合并 {{ dedupMergedCount }} 条重复注释</span>
        </h4>
        <svg class="map-svg" :viewBox="`0 0 ${MAP_W} ${mapHeight}`" preserveAspectRatio="xMidYMid meet" role="img">
          <!-- 刻度网格线 -->
          <line v-for="t in mapTicks" :key="'g' + t.pos" :x1="mapX(t.pos)" :x2="mapX(t.pos)"
                :y1="READS_TOP - 4" :y2="mapHeight - 2" class="map-grid" />
          <!-- read 行：深红块状箭头（方向见箭头），名字放得下画进箭头内，差异位点空心圆 -->
          <g v-for="r in mapRows" :key="r.index" class="map-row" @click="openReadInSeqviz(r.index)">
            <title>{{ r.filename }}：{{ r.ref_start }}-{{ r.ref_end }}（{{ r.direction === '+' ? '正向' : '反向' }}，一致性 {{ (r.identity * 100).toFixed(1) }}%）——点击在比对峰图中查看</title>
            <text v-if="!r.nameInside" :x="MAP_GUTTER - 8" :y="readY(r.lane) + READ_H / 2 + 4" text-anchor="end" class="map-label">
              {{ r.direction === '+' ? '→' : '←' }} {{ shortName(r.filename) }}
            </text>
            <path :d="arrowPath(mapX(r.ref_start), mapX(r.ref_start) + readWidth(r), readY(r.lane), READ_H, r.direction === '+')"
                  :class="r.direction === '+' ? 'map-arrow-fwd' : 'map-arrow-rev'" />
            <text v-if="r.nameInside" :x="mapX(r.ref_start) + (readWidth(r) - arrowHeadW(readWidth(r))) / 2"
                  :y="readY(r.lane) + READ_H / 2 + 4" text-anchor="middle" class="map-read-name">{{ shortName(r.filename) }}</text>
            <circle v-for="p in r.diffs" :key="p" :cx="mapX(p) + 1" :cy="readY(r.lane) + READ_H / 2" r="4.5" class="map-dot" />
          </g>
          <!-- 刻度轴：灰底 + 绿色已测覆盖段 + 黑轴线 + 刻度数字 -->
          <rect :x="MAP_GUTTER" :y="axisY - 4" :width="MAP_W - 10 - MAP_GUTTER" height="8" rx="4" class="map-axis-bg" />
          <rect v-for="(seg, i) in mapCovered" :key="'c' + i" :x="mapX(seg[0])" :y="axisY - 4"
                :width="Math.max(1.5, (seg[1] - seg[0] + 1) * mapScale)" height="8" class="map-axis-cov" />
          <line :x1="MAP_GUTTER" :x2="MAP_W - 10" :y1="axisY" :y2="axisY" class="map-axis-line" />
          <g v-for="t in mapTicks" :key="'t' + t.pos">
            <line :x1="mapX(t.pos)" :x2="mapX(t.pos)" :y1="axisY" :y2="axisY + 6" class="map-tick-line" />
            <text :x="mapX(t.pos)" :y="axisY + 18"
                  :text-anchor="mapX(t.pos) > MAP_W - 45 ? 'end' : (mapX(t.pos) < MAP_GUTTER + 45 ? 'start' : 'middle')"
                  class="map-tick-num">{{ t.label }}</text>
          </g>
          <!-- 轴上变异红块（点击跳峰图） -->
          <g v-for="v in analysis.variants" :key="'v' + v.ref_pos + v.type" class="map-var" @click.stop="jumpToVariant(v)">
            <title>{{ v.ref_pos }} {{ v.ref_base }}→{{ v.alt_base }}（{{ v.type === 'substitution' ? '替换' : v.type === 'insertion' ? '插入' : '缺失' }}，{{ v.support_reads || 1 }} 条 read）——点击在比对峰图中查看</title>
            <rect :x="mapX(v.ref_pos) - 3.5" :y="axisY - VAR_H + 2" width="7" height="10" rx="1" class="map-var-tick" />
          </g>
          <!-- 参考特征：彩色块状箭头，名字放不下时引线外置 -->
          <g v-for="(f, i) in mapFeats" :key="'f' + i" class="map-feat">
            <title>{{ f.name }}（{{ f.type }}，{{ f.start }}-{{ f.end }}，{{ f.strand === '-' ? '反向' : '正向' }}）</title>
            <path :d="arrowPath(f.x1, f.x2, featY(f.lane), FEAT_H, f.strand !== '-')"
                  :fill="mapFeatureColor(f.type)" :stroke="darkenColor(mapFeatureColor(f.type), 0.28)" stroke-width="0.8" />
            <text v-if="f.labelInside" :x="(f.x1 + f.x2) / 2" :y="featY(f.lane) + FEAT_H / 2 + 3.8"
                  text-anchor="middle" class="map-feat-label">{{ f.name }}</text>
            <template v-else>
              <line :x1="(f.x1 + f.x2) / 2" :x2="(f.x1 + f.x2) / 2" :y1="featY(f.lane) + FEAT_H"
                    :y2="labelY(f.labelLane) - 10" class="map-leader" />
              <text :x="f.labelX" :y="labelY(f.labelLane)" text-anchor="middle" class="map-feat-out">{{ f.name }}</text>
            </template>
          </g>
        </svg>
        <p class="map-legend hint">
          <span class="lg-read">▬ 测序 read（箭头=方向，点击在比对峰图中查看）</span> ·
          <span class="lg-dot">○ 差异位点</span> ·
          <span class="lg-var">▮</span> 变异（点击跳峰图） ·
          <span class="lg-cov">▬</span> 轴上绿段 = 已测序覆盖
          <template v-if="mapLegendTypes.length"> · 特征
            <span v-for="t in mapLegendTypes" :key="t.type" class="lg-type"><i :style="{ background: t.color }"></i>{{ t.type }}</span>
          </template>
        </p>
      </div>

      <!-- Read 摘要 -->
      <table class="seq-table" v-if="analysis.reads.length">
        <thead>
          <tr><th>文件</th><th>方向</th><th>比对区间</th><th>修剪后</th><th>平均Q</th><th>质量</th><th>一致性</th><th>证据查看</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in analysis.reads" :key="r.index">
            <td>{{ r.filename }}</td>
            <td>{{ r.direction === '+' ? '正向' : '反向' }}</td>
            <td>{{ r.ref_start }} - {{ r.ref_end }}</td>
            <td>{{ r.trimmed_length }} bp</td>
            <td>{{ r.mean_q }}</td>
            <td>
              <span v-if="r.grade" class="grade-chip" :class="'grade-' + r.grade"
                    :title="r.q20_ratio != null ? `Q20 比例 ${(r.q20_ratio * 100).toFixed(0)}%` : ''">
                {{ r.grade }}
              </span>
              <span v-else>-</span>
            </td>
            <td>{{ (r.identity * 100).toFixed(1) }}%</td>
            <td>
              <button class="mini-btn" @click="openReadInSeqviz(r.index)">查看</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="analysis.errors.length" class="error-msg">
        {{ analysis.errors.map((e) => `${e.filename}: ${e.error}`).join('；') }}
      </p>

      <!-- 突变表 -->
      <div v-if="analysis.variants.length">
        <h4 class="section-title">差异明细（点击行查看峰图）
          <button v-if="lowConfVariants.length" class="lowconf-toggle" @click="showLowConf = !showLowConf">
            {{ showLowConf ? '▾ 收起低置信' : `▸ ${lowConfVariants.length} 处低置信已折叠（疑似测序噪声/混合峰，展开逐条核对）` }}
          </button>
        </h4>
        <table class="seq-table clickable">
          <thead>
            <tr><th>位置</th><th>类型</th><th>变化</th><th>所在特征</th><th>氨基酸</th><th>移码</th><th>酶切位点</th><th>支持reads</th><th>Q</th><th>置信度</th></tr>
          </thead>
          <tbody>
            <tr v-for="v in shownVariants" :key="`${v.ref_pos}-${v.type}-${v.alt_base ?? ''}-${v.read ?? ''}`" @click="jumpToVariant(v)">
              <td>{{ v.ref_pos }}</td>
              <td>{{ v.type === 'substitution' ? '替换' : v.type === 'insertion' ? '插入' : '缺失' }}</td>
              <td class="mono">{{ v.ref_base }} → {{ v.alt_base }}</td>
              <td>{{ (v.features || []).map((f) => f.name).join(', ') || '非编码区' }}</td>
              <td>{{ v.aa_change || (v.type === 'substitution' ? '同义' : '-') }}</td>
              <td><span v-if="v.frameshift" class="badge bad">移码</span><span v-else>-</span></td>
              <td>
                <span v-if="v.enzyme_sites_lost?.length" class="badge bad">破坏: {{ v.enzyme_sites_lost.join(', ') }}</span>
                <span v-if="v.enzyme_sites_gained?.length" class="badge">新增: {{ v.enzyme_sites_gained.join(', ') }}</span>
                <span v-if="!v.enzyme_sites_lost?.length && !v.enzyme_sites_gained?.length">-</span>
              </td>
              <td>{{ v.support_reads || 1 }}</td>
              <td>{{ v.read_q ?? '-' }}</td>
              <td>
                <span v-if="v.confidence" class="conf-chip" :class="'conf-' + v.confidence"
                      :title="confTitle(v)">
                  {{ v.confidence === 'high' ? '高' : v.confidence === 'medium' ? '中' : '低' }}
                </span>
                <span v-else>-</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 比对峰图：参考行 + 各 read 碱基行 + 四通道峰图画在同一参考坐标轴上
           （差异证据一屏看完：参考碱基/read 碱基/Q/峰形/次级峰占比） -->
      <div class="seqviz-box" v-if="analysis.reads.length">
        <div class="trace-toolbar">
          <h4 class="section-title">比对峰图<span class="map-sub">（顶部覆盖简图常驻全景：每引物一条箭头按泳道排布，蓝框 = 当前视野，视野内引物着重，点击箭头选中该引物并放大到其覆盖区，点击空白跳到该位置；橙点 = 双峰位点，两端浅色 = 末端约 20bp 不可信区，黄刻度 = 低置信差异；主区参考行 + 各 read 字母行 + 单条峰图带，点字母行切峰图，Ctrl+滚轮缩放）</span></h4>
          <div class="seqviz-controls">
            <label v-for="r in analysis.reads" :key="r.index" class="seqviz-pick">
              <input type="checkbox" :checked="isReadVisible(r.index)" @change="toggleRead(r.index)" />{{ r.direction === '-' ? '←' : '→' }} {{ shortName(r.filename) }}
            </label>
            <button class="mini-btn" title="放大" @click="seqZoom(1.25)">＋</button>
            <button class="mini-btn" title="缩小" @click="seqZoom(0.8)">−</button>
            <button class="mini-btn" @click="seqFit">适应全宽</button>
            <input class="seqviz-jump" v-model="jumpInput" placeholder="参考位置" @keydown.enter="jumpToRefPos" />
            <button class="mini-btn" @click="jumpToRefPos">跳转</button>
          </div>
        </div>
        <p v-if="seqTraceLoading" class="hint">加载峰图…</p>
        <div ref="seqBox" class="seqviz-wrap" :style="{ height: seqWrapH + 'px' }"
             @scroll="onSeqScroll" @wheel="onSeqWheel" @click="onSeqClick">
          <div class="seqviz-spacer" :style="{ width: seqSpacerW + 'px' }"></div>
          <canvas ref="seqCanvas" class="seqviz-canvas"></canvas>
        </div>
        <p v-if="seqInfo" class="seqviz-info">{{ seqInfo }}</p>
        <p v-else class="hint">点击简图箭头选中引物（峰图随之切换并放大到该引物覆盖区），点简图空白跳到对应位置；点任意列查看各 read 在该位的碱基/质量/双峰证据；差异明细行与红块可跳到对应位置</p>
      </div>

      <!-- 解卷积结果 -->
      <div v-if="Object.keys(analysis.decomposed_alleles || {}).length" class="allele-box">
        <h4 class="section-title">混合样品解卷积结果（tracy）</h4>
        <div v-for="(alleles, fname) in analysis.decomposed_alleles" :key="fname" class="allele-item">
          <strong>{{ fname }}</strong>:
          <span v-for="(a, i) in alleles" :key="i" class="mono allele-seq">{{ a.sequence.slice(0, 60) }}…</span>
        </div>
      </div>

      <!-- 共识序列 -->
      <div class="consensus-box">
        <div class="trace-toolbar">
          <h4 class="section-title">拼接结果（Consensus，{{ analysis.consensus.sequence.length }} bp）</h4>
          <div>
            <button class="mini-btn" @click="downloadConsensus('fasta')">导出 FASTA</button>
            <button class="mini-btn" @click="downloadConsensus('genbank')">导出 GenBank</button>
          </div>
        </div>
        <pre class="consensus-pre"><span v-for="(s, i) in consensusSegments" :key="i" :class="{ 'cons-diff': s.diff }">{{ s.text }}</span></pre>
        <p v-if="analysis.consensus.diffs?.length" class="hint cons-hint">
          黄色高亮 = 共识序列与参考不同的位点（由测序证据投票写入，点击上方差异明细可核对峰图与比对）
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.seq-panel { display: flex; flex-direction: column; gap: 1.25rem; }

.upload-area {
  border: 2px dashed var(--border-color, #ddd);
  border-radius: 10px;
  padding: 1.5rem;
  text-align: center;
}
.upload-area.drag { border-color: var(--primary-color, #45B7D1); background: rgba(69,183,209,0.05); }
.upload-title { font-weight: 600; margin-bottom: 0.25rem; }
.upload-hint { font-size: 0.85rem; color: var(--text-secondary, #888); margin-bottom: 0.75rem; }
.upload-btns { display: flex; gap: 0.6rem; justify-content: center; margin-bottom: 0.75rem; }
.upload-btn {
  display: inline-block; padding: 0.5rem 1.2rem; background: var(--primary-color, #45B7D1);
  color: #fff; border-radius: 6px; cursor: pointer; font-size: 0.9rem;
}
.upload-btn.secondary { background: var(--text-secondary, #8aa0b4); }

.staged-files {
  display: flex; flex-direction: column; gap: 0.5rem; align-items: flex-start;
  max-width: 640px; margin: 0 auto; text-align: left;
}
.stage-label { flex-shrink: 0; font-size: 0.8rem; color: var(--text-secondary, #888); margin-right: 0.5rem; }
.ref-staged, .reads-staged { display: flex; flex-wrap: wrap; align-items: center; }
.file-chip.ref { background: #F0FAF2; border: 1px solid #BFE5C8; }
.stage-note { font-size: 0.78rem; color: #B26A00; margin: 0; }
.stage-empty { font-size: 0.85rem; color: var(--text-secondary, #999); margin: 0.25rem 0 0; }
.hint-missing { font-size: 0.8rem; color: #B26A00; margin: 0.35rem 0 0; }
.file-chip {
  background: var(--bg-secondary, #f5f5f5); padding: 0.25rem 0.6rem; border-radius: 999px;
  font-size: 0.8rem; display: inline-flex; align-items: center; gap: 0.35rem;
}
.file-remove { border: none; background: none; cursor: pointer; font-size: 1rem; color: #c00; }
.clear-btn {
  border: 1px solid var(--border-color, #ddd); background: #fff; color: #c0392b;
  border-radius: 999px; font-size: 0.72rem; padding: 0.1rem 0.55rem; cursor: pointer;
}
.clear-btn:hover { background: #FDE8E8; border-color: #E8A5A5; }
.advanced { margin-top: 0.75rem; font-size: 0.85rem; text-align: left; display: inline-block; }
.advanced label { display: block; margin: 0.35rem 0; }
.analyze-btn {
  display: block; margin: 1rem auto 0; padding: 0.6rem 2rem; border: none;
  background: #2E9E44; color: #fff; border-radius: 6px; cursor: pointer; font-size: 1rem;
}
.analyze-btn:disabled { background: #aaa; cursor: not-allowed; }
.error-msg { color: #c0392b; font-size: 0.85rem; margin-top: 0.5rem; }

.conclusion-card {
  background: #FDF3F3; border: 1px solid #F2C6C6; border-radius: 10px; padding: 1rem 1.25rem;
}
.conclusion-card.ok { background: #F0FAF2; border-color: #BFE5C8; }
.conclusion-text { font-weight: 600; white-space: pre-wrap; margin-bottom: 0.5rem; }
.lowconf-toggle {
  display: inline-block; margin: 0.1rem 0 0.4rem; padding: 0.15rem 0.6rem;
  font-size: 0.78rem; color: #8a6a1f; background: #FBF3DF;
  border: 1px solid #E8D9A8; border-radius: 12px; cursor: pointer;
}
.lowconf-toggle:hover { background: #F5E9C8; }
.lowconf-lines { font-weight: 400; font-size: 0.85rem; color: #8a6a1f; }
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
.cds-cov.uncovered { background: #EEE; color: #777; }
.cds-so { font-size: 0.7rem; padding: 1px 7px; border-radius: 10px; margin-left: 5px; vertical-align: 1px; }
.cds-so.high { background: #FBEAE8; color: #A03227; }
.cds-so.mid { background: #FDF2E3; color: #A8641A; }
.cds-so.low { background: #EDF2EE; color: #5E7A64; }
.cds-verdict { margin: 0.2rem 0 0; font-size: 0.86rem; }
.cds-detail { margin: 0.3rem 0 0; font-size: 0.78rem; color: #A03A2E; display: flex; flex-wrap: wrap; gap: 0.35rem 0.9rem; }
.poly-thresh {
  width: auto; min-width: 0; padding: 0 2px; font-size: 0.75rem; font-weight: 400;
  border: 1px solid #CBD5E1; border-radius: 4px; background: #fff; color: #334155;
  vertical-align: middle;
}
.poly-read-detail { color: #777; }
.conclusion-meta { display: flex; gap: 1.5rem; font-size: 0.8rem; color: #777; margin-bottom: 0.5rem; }
.coverage-bar {
  position: relative; height: 14px; background: #EEE; border-radius: 7px; overflow: hidden;
}
.coverage-seg { position: absolute; top: 0; bottom: 0; background: #2E9E44; }
.coverage-labels { display: flex; justify-content: space-between; font-size: 0.7rem; color: #999; margin-top: 2px; }

.seq-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.seq-table th, .seq-table td { padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border-color, #eee); text-align: left; }
.seq-table th { background: var(--bg-secondary, #f7f7f7); }
.seq-table.clickable tr { cursor: pointer; }
.seq-table.clickable tr:hover { background: var(--bg-secondary, #f7f7f7); }
.grade-chip { display: inline-block; min-width: 20px; text-align: center; font-weight: 700; border-radius: 6px; padding: 1px 7px; font-size: 0.8rem; }
.grade-A { background: #E5F5E9; color: #227A36; }
.grade-B { background: #FCF3DC; color: #9A6D00; }
.grade-C { background: #FBEAE8; color: #A03227; }
.conf-chip { display: inline-block; border-radius: 10px; padding: 1px 9px; font-size: 0.78rem; }
.conf-high { background: #E5F5E9; color: #227A36; }
.conf-medium { background: #FCF3DC; color: #9A6D00; }
.conf-low { background: #FBEAE8; color: #A03227; }
.coverage-gaps { margin: 0.4rem 0 0; font-size: 0.78rem; color: #9A6D00; }
.mono { font-family: Consolas, monospace; }

.section-title { font-size: 0.95rem; margin: 0 0 0.5rem; }
.badge { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 0.72rem; background: #EEE; }
.badge.bad { background: #FDE8E8; color: #C0392B; }

.allele-box { background: var(--bg-secondary, #f9f9f9); border-radius: 8px; padding: 0.75rem 1rem; }
.allele-item { font-size: 0.85rem; margin: 0.35rem 0; }
.allele-seq { margin: 0 0.75rem; }

.seqviz-box, .consensus-box { background: #fff; border: 1px solid var(--border-color, #eee); border-radius: 10px; padding: 0.75rem 1rem; }
.trace-toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.35rem; }
.hint { color: #999; font-size: 0.85rem; }

.mini-btn {
  border: 1px solid var(--border-color, #ddd); background: #fff; border-radius: 4px;
  font-size: 0.78rem; padding: 0.2rem 0.6rem; cursor: pointer; margin-left: 0.25rem;
}
.mini-btn:hover { background: var(--bg-secondary, #f5f5f5); }

.consensus-pre {
  font-family: Consolas, monospace; font-size: 0.72rem; line-height: 1.5;
  background: var(--bg-secondary, #f9f9f9); padding: 0.75rem; border-radius: 6px;
  max-height: 260px; overflow: auto; white-space: pre-wrap; word-break: break-all;
}
.cons-diff { background: #FFF3B8; border-radius: 2px; padding: 0 1px; }
.cons-hint { margin-top: 0.4rem; }

/* ==================== 比对峰图融合视图 ==================== */
.seqviz-controls { display: flex; flex-wrap: wrap; gap: 0.25rem; align-items: center; justify-content: flex-end; }
.seqviz-pick {
  font-size: 0.78rem; color: #444; white-space: nowrap;
  user-select: none; cursor: pointer; margin-right: 0.35rem;
}
.seqviz-pick input { vertical-align: middle; margin: 0 2px 0 0; }
.seqviz-jump {
  width: 70px; padding: 0.15rem 0.4rem; font-size: 0.78rem;
  border: 1px solid var(--border-color, #ddd); border-radius: 4px;
}
.seqviz-wrap {
  position: relative; overflow-x: auto; overflow-y: hidden;
  border: 1px solid #E5E8EC; border-radius: 6px; background: #fff; cursor: crosshair;
}
.seqviz-spacer { position: absolute; top: 0; left: 0; height: 1px; pointer-events: none; }
.seqviz-canvas { position: sticky; left: 0; top: 0; display: block; }
.seqviz-info { font-size: 0.8rem; color: #555; margin: 0.4rem 0 0; font-family: Consolas, monospace; }

/* ==================== 匹配简图 ==================== */
.map-box { background: #fff; border: 1px solid var(--border-color, #eee); border-radius: 10px; padding: 0.75rem 1rem; }
.map-sub { font-size: 0.75rem; color: #999; font-weight: 400; }
.map-dedup-toggle {
  font-size: 0.75rem; color: #555; font-weight: 400; margin-left: 0.75rem;
  display: inline-flex; align-items: center; gap: 0.25rem; cursor: pointer; user-select: none;
}
.map-dedup-toggle input { vertical-align: middle; }
.map-svg { width: 100%; height: auto; display: block; user-select: none; }
.map-label { font-size: 11.5px; fill: #444; font-family: Consolas, monospace; }
.map-grid { stroke: #ECEEF0; stroke-width: 1; stroke-dasharray: 3 4; }
.map-row { cursor: pointer; }
.map-read-name { font-size: 11.5px; fill: #fff; font-weight: 700; pointer-events: none; }
.map-arrow-fwd, .map-arrow-rev { fill: #B03A2E; stroke: #7E251C; stroke-width: 0.8; opacity: 0.92; }
.map-row:hover .map-arrow-fwd, .map-row:hover .map-arrow-rev { opacity: 1; fill: #C74A3C; }
.map-dot { fill: #fff; stroke: #C0392B; stroke-width: 1.5; pointer-events: none; }
.map-var { cursor: pointer; }
.map-var-tick { fill: #C0392B; }
.map-var:hover .map-var-tick { fill: #E74C3C; }
.map-axis-bg { fill: #D5D9DE; }
.map-axis-cov { fill: #58B368; }
.map-axis-line { stroke: #3A3F45; stroke-width: 1.6; }
.map-tick-line { stroke: #3A3F45; stroke-width: 1; }
.map-tick-num { font-size: 11px; fill: #555; }
.map-feat { fill-opacity: 0.95; }
.map-feat:hover { fill-opacity: 1; }
.map-feat-label { font-size: 10.5px; fill: #10131A; pointer-events: none; }
.map-feat-out { font-size: 11px; fill: #333; }
.map-leader { stroke: #A5A9AE; stroke-width: 0.8; }
.lg-read { color: #B03A2E; font-weight: 600; }
.lg-dot { color: #C0392B; font-weight: 600; }
.lg-var { color: #C0392B; font-weight: 700; }
.lg-cov { color: #58B368; font-weight: 700; }
.lg-type { margin-left: 6px; white-space: nowrap; }
.lg-type i { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 3px; vertical-align: -1px; }
.lg-fwd { color: #2E9E44; font-weight: 600; }
.lg-rev { color: #2456C8; font-weight: 600; }
.lg-dot { color: #C0392B; font-weight: 600; }
.lg-cov { color: #7DC98C; }
.lg-var { color: #C0392B; }
</style>
