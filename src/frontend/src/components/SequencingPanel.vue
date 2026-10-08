<script setup lang="ts">
/**
 * Sanger 测序全自动分析面板
 * 参考序列文件（.gb/.fasta/.dna）与 .ab1 放同一文件夹一起导入 →
 * 一键分析 → 总览结论 / 覆盖率 / 突变表 / 峰图 / 共识序列导出
 */
import { ref, computed, watch, nextTick } from 'vue'
import {
  analyzeSequencingFiles, exportConsensus, formatApiError,
  type SequencingAnalysis
} from '@/api'
import { downloadTextFile } from '@/utils/download'
import { shortName } from '@/utils/seqPanelModel'
import { useSeqViz } from '@/composables/useSeqViz'
import PolyCard from '@/components/sequencing/PolyCard.vue'
import ConclusionCards from '@/components/sequencing/ConclusionCards.vue'
import ReadTable from '@/components/sequencing/ReadTable.vue'
import VariantTable from '@/components/sequencing/VariantTable.vue'
import AlleleCard from '@/components/sequencing/AlleleCard.vue'
import ConsensusCard from '@/components/sequencing/ConsensusCard.vue'
import MatchMap from '@/components/sequencing/MatchMap.vue'

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
      // 行按视野过滤：把视口带到首条 read 的起点，避免初始面板没行
      nextTick(() => {
        const r0 = analysis.value?.reads[0]
        if (r0?.ref_start) scrollToRefPos(r0.ref_start)
      })
    }
    emit('analyzed', analysis.value)
  } catch (e: any) {
    errorMsg.value = formatApiError(e, '分析失败')
  } finally {
    analyzing.value = false
  }
}

// ==================== 比对峰图（SnapGene 式融合视图） ====================
// 状态/交互/绘制全量迁入 composables/useSeqViz.ts（composable 接收
// analysis/errorMsg 两个 ref）；组件侧解构后模板绑定与 wrapper.vm 访问
// 路径与拆分前一致。声明须在下方 preset 的 immediate watch 之前。
const seqviz = useSeqViz({ analysis, errorMsg })
const {
  seqBox, seqCanvas, visibleReads, seqTraceLoading,
  seqInfo, jumpInput, seqWrapH, seqSpacerW,
  selectedReadIdx, readColor, loadSeqTrace, isReadVisible, toggleRead,
  seqZoom, seqFit, jumpToRefPos, rowLayouts, jumpToVariant,
  openReadInSeqviz, onSeqScroll, onSeqWheel, onSeqClick, resetSeqViz,
  scrollToRefPos,
} = seqviz
// rowLayouts 仅测试经 wrapper.vm 调用（视野过滤断言）；seqColW/traceCache/
// selRefPos/seqScrollX 亦由 composable 返回、wrapper.vm 可访问
void rowLayouts

// 覆盖率条带/缺口摘要/结论分组/CDS 覆盖标签/SO 词表已迁入
// sequencing/ConclusionCards.vue（低置信折叠态经 defineModel 共享）

// ==================== 匹配简图（SnapGene 风格线性图谱） ====================
// 整块（read 箭头分道/刻度轴/参考特征/去重开关/图例）已迁入
// sequencing/MatchMap.vue；点击事件（open-read/jump-variant）回传父组件
// 走峰图联动。shortName 抽到 utils/seqPanelModel（简图标签/字母行芯片/
// 工具栏复选框三处共用）。

// ==================== 峰图数据加载 ====================
// readColor/峰图缓存加载/滚动缩放交互等已迁入 composables/useSeqViz.ts

// 低置信/双峰位点折叠态：结论卡（defineModel 共享）与突变表（defineModel
// 共享）用同一状态——总览里展开 → 突变表同步展开。须在 preset 的 immediate
// watch 之前声明，watch 里退出回看时会复位这两个状态
const showLowConf = ref(false)
const showMixedDetail = ref(false)
// 共识导出默认只取实测覆盖区（状态留在父组件，ConsensusCard 经 defineModel 共享）
const coveredOnly = ref(true)

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
      // 行按视野过滤：初始视口在参考开头，若首条 read 不在此处面板会没行
      nextTick(() => {
        const r0 = p.reads[0]
        if (r0?.ref_start) scrollToRefPos(r0.ref_start)
      })
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

// 低置信/差异明细过滤与置信度徽章悬停说明已迁入
// sequencing/VariantTable.vue（showLowConf 经 defineModel 共享）

// —— 峰图融合视图的其余逻辑（CHANNEL_COLORS/READ_COLORS/loadSeqTrace/
//    toggleRead/scrollTo/jump/selectRead/rowLayouts/交互事件/composeSeqInfo/
//    绘制 drawSeq 全家/重绘 watch/resize 生命周期）全部迁入
//    composables/useSeqViz.ts，本文件只保留模板用到的解构绑定 ——

// ==================== 共识序列 ====================
// 分段渲染（差异位高亮）与 coveredOnly 状态已迁入
// sequencing/ConsensusCard.vue（coveredOnly 经 defineModel 共享）

async function downloadConsensus(format: string) {
  const text = await exportConsensus(analysis.value!.analysis_id, format, coveredOnly.value)
  const ext = format === 'genbank' ? 'gb' : 'fasta'
  downloadTextFile(text, `consensus.${ext}`)
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
      <!-- 结论总览 + CDS 结论卡（子组件；低置信折叠态与突变表共享） -->
      <ConclusionCards v-model:show-low-conf="showLowConf" :analysis="analysis" />

      <!-- poly 同聚物/重复结构（子组件，含阈值筛选与逐 read 判读） -->
      <PolyCard :homopolymers="analysis.homopolymers ?? []" />

      <!-- 匹配简图（子组件；点击 read/变异红块回传给峰图联动） -->
      <MatchMap v-if="analysis.reads.length"
                :reads="analysis.reads" :variants="analysis.variants"
                :features="analysis.features" :coverage-ranges="analysis.coverage_ranges"
                :reference-length="analysis.reference_length"
                @open-read="openReadInSeqviz" @jump-variant="jumpToVariant" />

      <!-- Read 摘要（子组件；查看按钮回传给峰图联动） -->
      <ReadTable v-if="analysis.reads.length"
                 :reads="analysis.reads" :errors="analysis.errors"
                 @view="openReadInSeqviz" />

      <!-- 突变表（子组件；低置信折叠态与结论卡共享，点击行跳峰图） -->
      <VariantTable v-model:show-low-conf="showLowConf"
                    :variants="analysis.variants" @jump="jumpToVariant" />

      <!-- 比对峰图：参考行 + 各 read 碱基行 + 四通道峰图画在同一参考坐标轴上
           （差异证据一屏看完：参考碱基/read 碱基/Q/峰形/次级峰占比） -->
      <div class="seqviz-box" v-if="analysis.reads.length">
        <div class="trace-toolbar">
          <h4 class="section-title">比对峰图<span class="map-sub">（顶部覆盖简图按引物覆盖区缩放：每引物一条箭头按泳道排布、独立配色、无覆盖区不占位，蓝框 = 当前视野，视野内引物着重，点击箭头选中该引物并跳到其起点（峰图保持可读密度），点击空白跳到该位置；橙点 = 双峰位点，两端浅色 = 末端约 20bp 不可信区，黄刻度 = 低置信差异；主区参考行 + 视野内各 read 的字母行与其峰图条带交接排布（只显示覆盖当前视野的引物，行随横向滚动自动增减；选中者加高），点字母行或条带选中该 read，Ctrl+滚轮缩放）</span></h4>
          <div class="seqviz-controls">
            <label v-for="r in analysis.reads" :key="r.index" class="seqviz-pick"
                   :style="{ color: readColor(r.index) }">
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
        <p v-else class="hint">点击简图箭头选中引物并放大到其覆盖区，点简图空白跳到对应位置；只显示覆盖当前视野的引物（字母行下方直接衔接其峰图，随滚动自动增减），点字母行或条带选中它；点任意列查看各 read 在该位的碱基/质量/双峰证据；差异明细行与红块可跳到对应位置</p>
      </div>

      <!-- 解卷积结果（子组件） -->
      <AlleleCard v-if="analysis.decomposed_alleles"
                  :decomposed-alleles="analysis.decomposed_alleles" />

      <!-- 共识序列（子组件；coveredOnly 共享，导出动作回传父组件调 API） -->
      <ConsensusCard v-model:covered-only="coveredOnly"
                     :analysis="analysis" @download="downloadConsensus" />
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

/* 结论总览/CDS 卡样式 → sequencing/ConclusionCards.vue */
/* poly 卡样式 → sequencing/PolyCard.vue */
/* read 摘要表/突变表/解卷积/共识卡样式 → sequencing/ReadTable / VariantTable /
   AlleleCard / ConsensusCard.vue（scoped 样式不穿透子组件，需随模板各自携带） */

.section-title { font-size: 0.95rem; margin: 0 0 0.5rem; }

.seqviz-box { background: #fff; border: 1px solid var(--border-color, #eee); border-radius: 10px; padding: 0.75rem 1rem; }
.trace-toolbar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.35rem; }
.hint { color: #999; font-size: 0.85rem; }

.mini-btn {
  border: 1px solid var(--border-color, #ddd); background: #fff; border-radius: 4px;
  font-size: 0.78rem; padding: 0.2rem 0.6rem; cursor: pointer; margin-left: 0.25rem;
}
.mini-btn:hover { background: var(--bg-secondary, #f5f5f5); }

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

/* ==================== 匹配简图样式 → sequencing/MatchMap.vue ==================== */
.map-sub { font-size: 0.75rem; color: #999; font-weight: 400; }   /* 峰图工具栏说明文字仍用 */
</style>
