<script setup lang="ts">
/**
 * Sanger 测序全自动分析面板
 * 参考序列文件（.gb/.fasta/.dna）与 .ab1 放同一文件夹一起导入 →
 * 一键分析 → 总览结论 / 覆盖率 / 突变表 / 峰图 / 共识序列导出
 */
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
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
  fileError.value = ''
  for (const f of Array.from(list)) {
    const ext = fileExt(f.name)
    if (ext === 'ab1') {
      reads.value.push(f)
    } else if (REFERENCE_EXTS.includes(ext)) {
      if (referenceFile.value) conflictNames.value.push(f.name)
      else referenceFile.value = f
    } else {
      ignoredNames.value.push(f.name)
    }
  }
}

/** 从设计结果页/载体页深链进入时自动带入参考序列 */
watch(() => props.initialReference, (f) => {
  if (f) referenceFile.value = f
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
function clearReference() { referenceFile.value = null }

// ==================== 分析 ====================
const minQ = ref(20)
const allowDecompose = ref(true)
const analyzing = ref(false)
const analysis = ref<SequencingAnalysis | null>(null)
const errorMsg = ref('')

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
    errorMsg.value = e.response?.data?.detail || e.message || '分析失败'
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

// ==================== 峰图 ====================
const traceCanvas = ref<HTMLCanvasElement | null>(null)
const traceWrap = ref<HTMLDivElement | null>(null)
const activeRead = ref(0)
const trace = ref<ReadTrace | null>(null)
const traceLoading = ref(false)
const traceStart = ref(0)        // 显示窗口起始碱基（0-based）
const traceSpan = ref(60)        // 窗口碱基数
const highlightedPos = ref<number | null>(null) // 1-based 参考位置高亮

// 历史回看：注入已完成分析后直接展示（immediate 覆盖挂载时即带 preset 的场景）
watch(() => props.preset, (p) => {
  if (p) {
    analysis.value = p
    errorMsg.value = ''
    trace.value = null
    highlightedPos.value = null
  }
}, { immediate: true })

const CHANNEL_COLORS: Record<string, string> = { A: '#2E9E44', T: '#D0342C', G: '#222222', C: '#2456C8' }

async function loadTrace(readIndex: number) {
  activeRead.value = readIndex
  traceLoading.value = true
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

  // 高亮变异位置（参考坐标近似映射到 read 窗口）
  if (highlightedPos.value) {
    const rp = highlightedPos.value
    const aln = analysis.value?.reads[activeRead.value]
    if (aln && rp >= aln.ref_start && rp <= aln.ref_end) {
      const readIdx = rp - aln.ref_start // 近似：无 indel 时成立
      if (readIdx >= start && readIdx < end) {
        const x = left + (readIdx - start) * colW
        ctx.fillStyle = 'rgba(255, 220, 0, 0.25)'
        ctx.fillRect(x, peakTop, colW, h - peakTop)
      }
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
  if (activeRead.value !== read.index) await loadTrace(read.index)
  highlightedPos.value = v.ref_pos
  const aln = read
  const readIdx = v.ref_pos - aln.ref_start
  const t = trace.value
  if (t && readIdx >= 0 && readIdx < t.bases.length) {
    traceStart.value = Math.max(0, readIdx - Math.floor(traceSpan.value / 2))
  }
  nextDraw()
}

// ==================== 共识序列 ====================
const consensusView = computed(() => {
  const a = analysis.value
  if (!a) return ''
  return a.consensus.sequence.replace(/(.{60})/g, '$1\n')
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
          <span v-for="(f, i) in reads" :key="i" class="file-chip">
            {{ f.name }} ({{ (f.size / 1024).toFixed(0) }}KB)
            <button class="file-remove" @click="removeRead(i)">×</button>
          </span>
        </div>
        <p v-if="conflictNames.length" class="stage-note">已忽略多余的参考文件：{{ conflictNames.join('、') }}（一次只能分析一个参考序列）</p>
        <p v-if="ignoredNames.length" class="stage-note">已忽略无关文件：{{ ignoredNames.slice(0, 5).join('、') }}{{ ignoredNames.length > 5 ? ' 等' : '' }}</p>
      </div>
      <p v-else class="stage-empty">尚未选择文件：需要 1 个参考序列文件 + 至少 1 个 .ab1</p>
      <p v-if="fileError" class="error-msg">{{ fileError }}</p>

      <details class="advanced">
        <summary>高级参数</summary>
        <label>末端修剪 Q 阈值 <input type="number" v-model.number="minQ" min="5" max="40" /></label>
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

      <!-- Read 摘要 -->
      <table class="seq-table" v-if="analysis.reads.length">
        <thead>
          <tr><th>文件</th><th>方向</th><th>比对区间</th><th>修剪后</th><th>平均Q</th><th>一致性</th><th>峰图</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in analysis.reads" :key="r.index">
            <td>{{ r.filename }}</td>
            <td>{{ r.direction === '+' ? '正向' : '反向' }}</td>
            <td>{{ r.ref_start }} - {{ r.ref_end }}</td>
            <td>{{ r.trimmed_length }} bp</td>
            <td>{{ r.mean_q }}</td>
            <td>{{ (r.identity * 100).toFixed(1) }}%</td>
            <td><button class="mini-btn" @click="loadTrace(r.index)">查看</button></td>
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
        <pre class="consensus-pre">{{ consensusView }}</pre>
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
</style>
