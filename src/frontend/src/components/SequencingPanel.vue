<script setup lang="ts">
/**
 * Sanger 测序全自动分析面板
 * 参考序列文件（.gb/.fasta/.dna）与 .ab1 放同一文件夹一起导入 →
 * 一键分析 → 总览结论 / 覆盖率 / 突变表 / 峰图 / 共识序列导出
 */
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import {
  analyzeSequencingFiles, getReadTrace, exportConsensus,
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
function removeRead(i: number) { reads.value.splice(i, 1) }
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
    emit('analyzed', analysis.value)
  } catch (e: any) {
    // FastAPI 校验错误(422)的 detail 是数组，逐条转成可读文本
    const detail = e.response?.data?.detail
    if (typeof detail === 'string') errorMsg.value = detail
    else if (Array.isArray(detail)) errorMsg.value = detail.map((d: any) => d.msg || JSON.stringify(d)).join('；')
    else errorMsg.value = e.message || '分析失败'
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

// ==================== 比对校验（Read vs Reference 逐碱基核对） ====================
// 注意：必须在下方 preset 的 immediate watch 之前声明（watch 回调引用 focusCol）
const ALIGN_CHUNK = 60   // 每行显示列数
const alignReadIdx = ref(0)
const focusCol = ref<number | null>(null)  // 点击差异行后高亮的全局列号
const chunkEls: Record<number, HTMLElement | null> = {}
const alignBox = ref<HTMLElement | null>(null)

const currentRead = computed(() => analysis.value?.reads[alignReadIdx.value] || null)
const alignmentView = computed(() => currentRead.value?.alignment_view || null)

interface AlnCol { ref: string; read: string; q: number; mm: boolean; indel: boolean; refPos: number }

const alignmentCols = computed<AlnCol[]>(() => {
  const av = alignmentView.value
  if (!av) return []
  const cols: AlnCol[] = []
  let refPos = av.ref_start
  for (let i = 0; i < av.ref_aligned.length; i++) {
    const rb = av.ref_aligned[i]
    const qb = av.read_aligned[i]
    const col: AlnCol = { ref: rb, read: qb, q: av.q_aligned?.[i] ?? 0, mm: false, indel: false, refPos: 0 }
    if (rb !== '-') col.refPos = refPos++
    col.indel = rb === '-' || qb === '-'
    col.mm = !col.indel && rb !== qb
    cols.push(col)
  }
  return cols
})

const alignmentChunks = computed(() => {
  const cols = alignmentCols.value
  const out: { startCol: number; cols: AlnCol[] }[] = []
  for (let i = 0; i < cols.length; i += ALIGN_CHUNK) {
    out.push({ startCol: i, cols: cols.slice(i, i + ALIGN_CHUNK) })
  }
  return out
})

function setChunkRef(i: number, el: unknown) {
  chunkEls[i] = (el as HTMLElement) || null
}

function selectAlignRead(i: number) {
  alignReadIdx.value = i
  focusCol.value = null
}

function chunkStartPos(chunk: { cols: AlnCol[] }): string {
  const first = chunk.cols.find((c) => c.refPos)
  return first ? String(first.refPos) : '—'
}

function qLabel(c: AlnCol): string {
  if (c.read === '-') return '—'
  return c.q > 0 ? String(c.q) : '·'
}

/** 定位到指定参考位置的列；插入差异（afterGap）锚定在左翼参考位置之后 */
function focusAlignmentAt(refPos: number, afterGap: boolean) {
  const cols = alignmentCols.value
  let anchor = -1
  for (let i = 0; i < cols.length; i++) {
    if (cols[i].refPos === refPos && cols[i].ref !== '-') { anchor = i; break }
  }
  if (anchor < 0) return
  let target = anchor
  if (afterGap) {
    target = anchor + 1
    while (target < cols.length && cols[target].ref === '-') target++
    target = Math.min(target, cols.length - 1)
  }
  focusCol.value = target
  chunkEls[Math.floor(target / ALIGN_CHUNK)]?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
}

// ==================== 匹配简图（多 read 落位一览） ====================
const MAP_W = 1000        // viewBox 宽度
const MAP_GUTTER = 150    // 左侧文件名栏宽
const MAP_ROW_H = 22      // 每行高度
const MAP_BAR_H = 10      // 条带高度
const MAP_TOP = 34        // 参考行 + 刻度占用的顶部高度

type MapRead = SequencingAnalysis['reads'][number] & { diffs: number[] }

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

/** 按落位排序的 read 行（附差异位置） */
const mapRows = computed<MapRead[]>(() => {
  const a = analysis.value
  if (!a) return []
  return [...a.reads]
    .sort((x, y) => x.ref_start - y.ref_start || x.index - y.index)
    .map((r) => ({ ...r, diffs: readDiffPositions(r) }))
})

const mapHeight = computed(() => MAP_TOP + mapRows.value.length * MAP_ROW_H + 4)
const mapScale = computed(() => {
  const a = analysis.value
  return a ? (MAP_W - 10 - MAP_GUTTER) / a.reference_length : 0
})

function mapX(pos: number): number {
  return MAP_GUTTER + (pos - 1) * mapScale.value
}

function mapRowY(i: number): number {
  return MAP_TOP + i * MAP_ROW_H
}

/** 参考条覆盖段（灰底上叠加绿色已测段） */
const mapCovered = computed(() => analysis.value?.coverage_ranges ?? [])

/** 参考条坐标刻度（首/25%/50%/75%/尾） */
const mapTicks = computed(() => {
  const a = analysis.value
  if (!a) return []
  const L = a.reference_length
  const ticks = [{ pos: 1, label: '1' }]
  for (const f of [0.25, 0.5, 0.75]) {
    ticks.push({ pos: Math.round(L * f), label: String(Math.round(L * f)) })
  }
  ticks.push({ pos: L, label: String(L) })
  return ticks
})

function shortName(name: string): string {
  return name.length > 18 ? name.slice(0, 17) + '…' : name
}

// ==================== 峰图 ====================
const traceCanvas = ref<HTMLCanvasElement | null>(null)
const traceWrap = ref<HTMLDivElement | null>(null)
const activeRead = ref(0)
const trace = ref<ReadTrace | null>(null)
const traceLoading = ref(false)
const traceStart = ref(0)        // 显示窗口起始碱基（0-based）
const traceSpan = ref(60)        // 窗口碱基数
const highlightReadIdx = ref<number | null>(null) // 高亮的 read 碱基（0-based，精确坐标）

// 历史回看：注入已完成分析后直接展示（immediate 覆盖挂载时即带 preset 的场景）
watch(() => props.preset, (p) => {
  if (p) {
    analysis.value = p
    errorMsg.value = ''
    trace.value = null
    highlightReadIdx.value = null
    focusCol.value = null
  }
}, { immediate: true })

const CHANNEL_COLORS: Record<string, string> = { A: '#2E9E44', T: '#D0342C', G: '#222222', C: '#2456C8' }

async function loadTrace(readIndex: number) {
  activeRead.value = readIndex
  traceLoading.value = true
  highlightReadIdx.value = null
  try {
    trace.value = await getReadTrace(analysis.value!.analysis_id, readIndex)
    traceStart.value = 0
    nextDraw()
  } catch (e: any) {
    errorMsg.value = e.response?.data?.detail || '峰图加载失败'
  } finally {
    traceLoading.value = false
  }
}

function nextDraw() { requestAnimationFrame(drawTrace) }

function drawTrace() {
  const canvas = traceCanvas.value
  const wrap = traceWrap.value
  const t = trace.value
  if (!canvas || !wrap || !t) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const dpr = window.devicePixelRatio || 1
  const w = wrap.clientWidth
  const h = wrap.clientHeight
  canvas.width = w * dpr
  canvas.height = h * dpr
  canvas.style.width = `${w}px`
  canvas.style.height = `${h}px`
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, h)

  const bases = t.bases
  const span = Math.min(traceSpan.value, Math.max(10, bases.length))
  const start = Math.min(traceStart.value, Math.max(0, bases.length - span))
  const end = Math.min(bases.length, start + span)
  const colW = (w - 70) / span

  // 窗口内四通道最大值（归一化）
  let maxV = 1
  for (const b of ['A', 'T', 'G', 'C']) {
    for (const v of (t.channels as Record<string, number[]>)[b].slice(start * 10, end * 10)) if (v > maxV) maxV = v
  }
  const peakTop = 24
  const peakH = h - peakTop - 34
  const left = 60

  const mixedSet = new Set(analysis.value?.reads[activeRead.value]?.mixed_positions || [])

  // trace 曲线：每个碱基约 10 个采样点
  const pointsPerBase = 10
  const traceLen = Math.min(t.channels.A.length, end * pointsPerBase)

  for (const b of ['A', 'T', 'G', 'C']) {
    ctx.beginPath()
    ctx.strokeStyle = CHANNEL_COLORS[b]
    ctx.lineWidth = 1.2
    let started = false
    const from = Math.max(0, start * pointsPerBase)
    for (let i = from; i < traceLen; i++) {
      const baseIdx = i / pointsPerBase
      const x = left + (baseIdx - start) * colW
      const y = peakTop + peakH * (1 - (t.channels as Record<string, number[]>)[b][i] / maxV)
      if (!started) { ctx.moveTo(x, y); started = true } else ctx.lineTo(x, y)
    }
    ctx.stroke()
  }

  // 碱基字母 + 质量着色 + 位置刻度
  ctx.font = '11px Consolas, monospace'
  ctx.textAlign = 'center'
  for (let i = start; i < end; i++) {
    const x = left + (i - start) * colW + colW / 2
    const base = bases[i]
    if (base === ' ') continue
    const q = t.quality[i] ?? 0
    ctx.fillStyle = q < 20 ? '#D0342C' : '#333'
    ctx.fillText(base, x, h - 18)
    if ((i + 1) % 10 === 0) {
      ctx.fillStyle = '#AAA'
      ctx.font = '9px Arial'
      ctx.fillText(String(i + 1), x, h - 4)
      ctx.font = '11px Consolas, monospace'
    }
    // 混合位点标记
    if (mixedSet.has(i + 1)) {
      ctx.strokeStyle = '#E67E22'
      ctx.strokeRect(left + (i - start) * colW, peakTop, colW, peakH)
    }
  }

  // 高亮变异位置（read_pos 精确坐标，含 indel 也准确）
  if (highlightReadIdx.value != null) {
    const readIdx = highlightReadIdx.value
    if (readIdx >= start && readIdx < end) {
      const x = left + (readIdx - start) * colW
      ctx.fillStyle = 'rgba(255, 220, 0, 0.25)'
      ctx.fillRect(x, peakTop, colW, h - peakTop)
    }
  }

  // 图例
  ctx.font = '10px Arial'
  ctx.textAlign = 'left'
  let lx = 4
  for (const b of ['A', 'T', 'G', 'C']) {
    ctx.fillStyle = CHANNEL_COLORS[b]
    ctx.fillText(b, lx, 12)
    lx += 14
  }
}

function traceShift(dir: number) {
  const t = trace.value
  if (!t) return
  traceStart.value = Math.max(0, Math.min(t.bases.length - traceSpan.value, traceStart.value + dir * Math.floor(traceSpan.value / 2)))
  nextDraw()
}
function traceZoom(factor: number) {
  traceSpan.value = Math.max(15, Math.min(300, Math.round(traceSpan.value * factor)))
  nextDraw()
}

async function jumpToVariant(v: SequencingVariant) {
  if (!analysis.value) return
  const read = analysis.value.reads.find((r) => r.filename === (v.read || r.filename)) || analysis.value.reads[0]
  if (!trace.value || activeRead.value !== read.index) await loadTrace(read.index)
  const t = trace.value
  // 精确定位：read_pos 是该 read 修剪后序列内的 1-based 位置（有 indel 也准确）
  const readIdx = v.read_pos ? v.read_pos - 1 : v.ref_pos - read.ref_start
  if (t && readIdx >= 0 && readIdx < t.bases.length) {
    traceStart.value = Math.max(0, readIdx - Math.floor(traceSpan.value / 2))
  }
  highlightReadIdx.value = readIdx >= 0 ? readIdx : null
  // 比对视图同步定位到该差异列
  alignReadIdx.value = read.index
  await nextTick()
  focusAlignmentAt(v.ref_pos, v.type === 'insertion')
  nextDraw()
}

function showAlignment(i: number) {
  selectAlignRead(i)
  nextTick(() => alignBox.value?.scrollIntoView?.({ block: 'start', behavior: 'smooth' }))
}

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

onMounted(() => window.addEventListener('resize', nextDraw))
onBeforeUnmount(() => window.removeEventListener('resize', nextDraw))
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
          <span v-for="(f, i) in reads" :key="i" class="file-chip">
            {{ f.name }} ({{ (f.size / 1024).toFixed(0) }}KB)
            <button class="file-remove" @click="removeRead(i)">×</button>
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
        <p class="conclusion-text">{{ analysis.conclusion }}</p>
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
      </div>

      <!-- 匹配简图：多 read 在参考上的落位与差异一览 -->
      <div class="map-box" v-if="analysis.reads.length">
        <h4 class="section-title">匹配简图<span class="map-sub">（每条 read 的落位与差异；点击条带看比对，点击红块看峰图）</span></h4>
        <svg class="map-svg" :viewBox="`0 0 ${MAP_W} ${mapHeight}`" preserveAspectRatio="xMidYMid meet" role="img">
          <!-- 刻度网格线 -->
          <line v-for="t in mapTicks" :key="'g' + t.pos" :x1="mapX(t.pos)" :x2="mapX(t.pos)"
                y1="27" :y2="mapHeight - 4" class="map-grid" />
          <!-- 参考条（灰底 + 绿色已覆盖段） -->
          <text :x="MAP_GUTTER - 8" y="15" text-anchor="end" class="map-label">参考</text>
          <rect :x="MAP_GUTTER" y="9" :width="MAP_W - 10 - MAP_GUTTER" height="6" rx="3" class="map-ref-bg" />
          <rect v-for="(seg, i) in mapCovered" :key="'c' + i" :x="mapX(seg[0])" y="9"
                :width="Math.max(1.5, (seg[1] - seg[0] + 1) * mapScale)" height="6" class="map-ref-cov" />
          <!-- 刻度数字 -->
          <text v-for="(t, i) in mapTicks" :key="'t' + t.pos" :x="mapX(t.pos)" y="24"
                :text-anchor="i === 0 ? 'start' : (i === mapTicks.length - 1 ? 'end' : 'middle')"
                class="map-tick">{{ t.label }}</text>
          <!-- 合并后的差异标记（参考条上方红块，点击跳峰图） -->
          <g v-for="v in analysis.variants" :key="'v' + v.ref_pos + v.type" class="map-var" @click.stop="jumpToVariant(v)">
            <title>{{ v.ref_pos }} {{ v.ref_base }}→{{ v.alt_base }}（{{ v.type === 'substitution' ? '替换' : v.type === 'insertion' ? '插入' : '缺失' }}，{{ v.support_reads || 1 }} 条 read）——点击查看峰图</title>
            <rect :x="mapX(v.ref_pos) - 2" y="1" width="4" height="7" rx="1" class="map-var-tick" />
          </g>
          <!-- read 行 -->
          <g v-for="(r, i) in mapRows" :key="r.index" class="map-row" @click="showAlignment(r.index)">
            <title>{{ r.filename }}：{{ r.ref_start }}-{{ r.ref_end }}（{{ r.direction === '+' ? '正向' : '反向' }}，一致性 {{ (r.identity * 100).toFixed(1) }}%）——点击查看逐碱基比对</title>
            <text :x="MAP_GUTTER - 8" :y="mapRowY(i) + MAP_BAR_H / 2 + 3.5" text-anchor="end" class="map-label">
              {{ r.direction === '+' ? '→' : '←' }} {{ shortName(r.filename) }}
            </text>
            <rect :x="mapX(r.ref_start)" :y="mapRowY(i)"
                  :width="Math.max(2, (r.ref_end - r.ref_start + 1) * mapScale)" :height="MAP_BAR_H" rx="3"
                  :class="r.direction === '+' ? 'map-bar-fwd' : 'map-bar-rev'" />
            <circle v-for="p in r.diffs" :key="p" :cx="mapX(p) + 1" :cy="mapRowY(i) + MAP_BAR_H / 2" r="2.4" class="map-dot" />
          </g>
        </svg>
        <p class="map-legend hint">
          <span class="lg-fwd">━ 正向</span> · <span class="lg-rev">━ 反向</span> ·
          <span class="lg-dot">○ 差异位点</span> · <span class="lg-cov">▮</span> 参考条绿段 = 已测序覆盖 ·
          <span class="lg-var">▮</span> 红块 = 差异（点击跳峰图）
        </p>
      </div>

      <!-- Read 摘要 -->
      <table class="seq-table" v-if="analysis.reads.length">
        <thead>
          <tr><th>文件</th><th>方向</th><th>比对区间</th><th>修剪后</th><th>平均Q</th><th>一致性</th><th>证据查看</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in analysis.reads" :key="r.index">
            <td>{{ r.filename }}</td>
            <td>{{ r.direction === '+' ? '正向' : '反向' }}</td>
            <td>{{ r.ref_start }} - {{ r.ref_end }}</td>
            <td>{{ r.trimmed_length }} bp</td>
            <td>{{ r.mean_q }}</td>
            <td>{{ (r.identity * 100).toFixed(1) }}%</td>
            <td>
              <button class="mini-btn" @click="loadTrace(r.index)">峰图</button>
              <button class="mini-btn" @click="showAlignment(r.index)">比对</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="analysis.errors.length" class="error-msg">
        {{ analysis.errors.map((e) => `${e.filename}: ${e.error}`).join('；') }}
      </p>

      <!-- 突变表 -->
      <div v-if="analysis.variants.length">
        <h4 class="section-title">差异明细（点击行查看峰图）</h4>
        <table class="seq-table clickable">
          <thead>
            <tr><th>位置</th><th>类型</th><th>变化</th><th>所在特征</th><th>氨基酸</th><th>移码</th><th>酶切位点</th><th>支持reads</th><th>Q</th></tr>
          </thead>
          <tbody>
            <tr v-for="(v, i) in analysis.variants" :key="i" @click="jumpToVariant(v)">
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
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 比对校验：read vs 参考逐碱基核对（差异/插入缺失/低质量一目了然） -->
      <div class="align-box" ref="alignBox" v-if="analysis.reads.length">
        <div class="trace-toolbar">
          <h4 class="section-title">比对校验<span v-if="currentRead"> — {{ currentRead.filename }}</span></h4>
          <div class="aln-read-picker" v-if="analysis.reads.length > 1">
            <button v-for="r in analysis.reads" :key="r.index" class="mini-btn"
                    :class="{ active: alignReadIdx === r.index }" @click="selectAlignRead(r.index)">
              {{ r.filename }}
            </button>
          </div>
        </div>
        <p class="aln-legend" v-if="alignmentView && currentRead">
          {{ currentRead.direction === '-' ? '反向 read（以参考方向展示，即测序碱基的反向互补）' : '正向 read' }}
          · 参考区间 {{ currentRead.ref_start }}-{{ currentRead.ref_end }}
          · 一致性 {{ (currentRead.identity * 100).toFixed(1) }}%
          · <span class="lg-mm">红底 = 与参考不同</span>
          · <span class="lg-q">橙字 = Q&lt;20 低质量</span>
          · — = 插入/缺失
        </p>
        <div class="aln-scroll" v-if="alignmentChunks.length">
          <div v-for="(chunk, ci) in alignmentChunks" :key="ci" class="aln-chunk" :ref="(el) => setChunkRef(ci, el)">
            <div class="aln-row ruler">
              <span class="aln-lbl">{{ chunkStartPos(chunk) }}</span>
              <span v-for="(c, i) in chunk.cols" :key="i" class="cell"
                    :class="{ tick: c.refPos && c.refPos % 10 === 0 }">{{ c.refPos && c.refPos % 10 === 0 ? (c.refPos % 10) : '' }}</span>
            </div>
            <div class="aln-row">
              <span class="aln-lbl">参考</span>
              <span v-for="(c, i) in chunk.cols" :key="i" class="cell mono"
                    :class="{ gap: c.ref === '-', focus: focusCol === chunk.startCol + i }">{{ c.ref }}</span>
            </div>
            <div class="aln-row">
              <span class="aln-lbl">Read</span>
              <span v-for="(c, i) in chunk.cols" :key="i" class="cell mono"
                    :class="{ gap: c.read === '-', mm: c.mm, indel: c.indel && c.read !== '-', qLow: c.read !== '-' && c.q > 0 && c.q < 20, focus: focusCol === chunk.startCol + i }">{{ c.read }}</span>
            </div>
            <div class="aln-row q-row">
              <span class="aln-lbl">Q</span>
              <span v-for="(c, i) in chunk.cols" :key="i" class="cell qcell"
                    :class="{ qLow: c.read !== '-' && c.q > 0 && c.q < 20, gap: c.read === '-' }">{{ qLabel(c) }}</span>
            </div>
          </div>
        </div>
        <p v-else class="hint">该 read 无对齐数据</p>
      </div>

      <!-- 解卷积结果 -->
      <div v-if="Object.keys(analysis.decomposed_alleles || {}).length" class="allele-box">
        <h4 class="section-title">混合样品解卷积结果（tracy）</h4>
        <div v-for="(alleles, fname) in analysis.decomposed_alleles" :key="fname" class="allele-item">
          <strong>{{ fname }}</strong>:
          <span v-for="(a, i) in alleles" :key="i" class="mono allele-seq">{{ a.sequence.slice(0, 60) }}…</span>
        </div>
      </div>

      <!-- 峰图 -->
      <div class="trace-box">
        <div class="trace-toolbar">
          <h4 class="section-title">Chromatogram{{ trace ? ` — ${trace.filename}` : '' }}</h4>
          <div class="trace-controls">
            <button class="mini-btn" @click="traceShift(-1)">←</button>
            <button class="mini-btn" @click="traceZoom(0.7)">放大</button>
            <button class="mini-btn" @click="traceZoom(1.4)">缩小</button>
            <button class="mini-btn" @click="traceShift(1)">→</button>
          </div>
        </div>
        <p v-if="traceLoading" class="hint">加载峰图…</p>
        <div v-else-if="trace" ref="traceWrap" class="trace-wrap">
          <canvas ref="traceCanvas"></canvas>
        </div>
        <p v-else class="hint">点击 read 表中的「查看」或差异明细行来加载峰图</p>
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
.mono { font-family: Consolas, monospace; }

.section-title { font-size: 0.95rem; margin: 0 0 0.5rem; }
.badge { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 0.72rem; background: #EEE; }
.badge.bad { background: #FDE8E8; color: #C0392B; }

.allele-box { background: var(--bg-secondary, #f9f9f9); border-radius: 8px; padding: 0.75rem 1rem; }
.allele-item { font-size: 0.85rem; margin: 0.35rem 0; }
.allele-seq { margin: 0 0.75rem; }

.trace-box, .consensus-box { background: #fff; border: 1px solid var(--border-color, #eee); border-radius: 10px; padding: 0.75rem 1rem; }
.trace-toolbar { display: flex; justify-content: space-between; align-items: center; }
.trace-controls { display: flex; gap: 0.35rem; }
.trace-wrap { height: 220px; border: 1px solid #f0f0f0; border-radius: 6px; overflow: hidden; }
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

/* ==================== 比对校验视图 ==================== */
.align-box { background: #fff; border: 1px solid var(--border-color, #eee); border-radius: 10px; padding: 0.75rem 1rem; }
.aln-read-picker { display: flex; flex-wrap: wrap; gap: 0.25rem; justify-content: flex-end; }
.aln-read-picker .mini-btn.active { background: #2E9E44; color: #fff; border-color: #2E9E44; }
.aln-legend { font-size: 0.78rem; color: #777; margin: 0.4rem 0 0.5rem; }
.lg-mm { color: #C0392B; font-weight: 600; }
.lg-q { color: #E67E22; font-weight: 600; }
.aln-scroll { overflow-x: auto; }
.aln-chunk { display: block; padding: 0.1rem 0.5rem 0.3rem 0; border-bottom: 1px dashed #ECECEC; }
.aln-chunk:last-child { border-bottom: none; }
.aln-row { display: flex; align-items: baseline; white-space: nowrap; line-height: 1.5; }
.aln-lbl {
  display: inline-block; width: 120px; flex-shrink: 0;
  font-size: 0.7rem; color: #999; text-align: right; padding-right: 8px;
  font-family: Consolas, monospace;
}
.cell { display: inline-block; width: 11px; text-align: center; font-size: 11px; line-height: 1.5; }
.cell.mono { font-family: Consolas, monospace; }
.ruler .cell { font-size: 9px; color: #B8B8B8; }
.q-row .qcell { font-size: 7.5px; color: #999; }
.cell.gap { color: #C8C8C8; }
.cell.mm { background: #FDE8E8; color: #C0392B; font-weight: 700; border-radius: 2px; }
.cell.indel { background: #FDE8E8; color: #C0392B; border-radius: 2px; }
.cell.qLow { color: #E67E22; }
.qcell.qLow { color: #E67E22; font-weight: 700; }
.cell.focus { outline: 2px solid #F1C40F; outline-offset: -1px; background: rgba(241, 196, 15, 0.18); }

/* ==================== 匹配简图 ==================== */
.map-box { background: #fff; border: 1px solid var(--border-color, #eee); border-radius: 10px; padding: 0.75rem 1rem; }
.map-sub { font-size: 0.75rem; color: #999; font-weight: 400; }
.map-svg { width: 100%; height: auto; display: block; user-select: none; }
.map-label { font-size: 11px; fill: #666; font-family: Consolas, monospace; }
.map-tick { font-size: 9px; fill: #AAA; }
.map-grid { stroke: #F0F0F0; stroke-width: 1; stroke-dasharray: 3 3; }
.map-ref-bg { fill: #E7E7E7; }
.map-ref-cov { fill: #7DC98C; }
.map-row { cursor: pointer; }
.map-bar-fwd { fill: #2E9E44; opacity: 0.85; }
.map-bar-rev { fill: #2456C8; opacity: 0.8; }
.map-row:hover .map-bar-fwd, .map-row:hover .map-bar-rev { opacity: 1; }
.map-dot { fill: #fff; stroke: #C0392B; stroke-width: 1.2; pointer-events: none; }
.map-var { cursor: pointer; }
.map-var-tick { fill: #C0392B; }
.map-var:hover .map-var-tick { fill: #E74C3C; }
.lg-fwd { color: #2E9E44; font-weight: 600; }
.lg-rev { color: #2456C8; font-weight: 600; }
.lg-dot { color: #C0392B; font-weight: 600; }
.lg-cov { color: #7DC98C; }
.lg-var { color: #C0392B; }
</style>
