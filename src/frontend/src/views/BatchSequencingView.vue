<script setup lang="ts">
/**
 * 批量测序分析 — 独立入口（单样品分析见 /sequencing）
 * 测序公司交付常为「Excel 信息表 + 一批 .ab1/.dna」：整个文件夹一次上传，
 * 按质粒归组（信息表引物列/质粒名，缺省时按图谱文件名包含关系）逐个跑
 * 全自动管线，一句话结论速览；详情深链到 /sequencing?history=<analysis_id>
 */
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { analyzeSequencingBatch, type SequencingBatchItem, type SequencingBatchResult } from '@/api'

const router = useRouter()

// ==================== 文件导入（支持整个文件夹拖入） ====================
const files = ref<File[]>([])
const excelFile = ref<File | null>(null)
const ignoredNames = ref<string[]>([])
const errorMsg = ref('')
const dragOver = ref(false)

function fileExt(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1).toLowerCase() : ''
}

function addFiles(list: File[] | FileList | null | undefined) {
  if (!list) return
  ignoredNames.value = []
  errorMsg.value = ''
  for (const f of Array.from(list)) {
    if (fileExt(f.name) === 'xlsx') {
      if (!excelFile.value) excelFile.value = f
      continue
    }
    files.value.push(f)
  }
}

function clearFiles() { files.value = [] }
function clearExcel() { excelFile.value = null }

// 拖入文件夹时浏览器给的是目录句柄，需要递归枚举成 File 列表
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
    walkEntries(entries).then((fs) => addFiles(fs))
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
function onExcelPick(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) excelFile.value = f
  ;(e.target as HTMLInputElement).value = ''
}

// ==================== 分析 ====================
const minQ = ref(20)
const analyzing = ref(false)
const result = ref<SequencingBatchResult | null>(null)

function normalizeMinQ() {
  if (!Number.isFinite(minQ.value)) { minQ.value = 20; return }
  minQ.value = Math.max(0, Math.min(60, Math.round(minQ.value)))
}

const canAnalyze = computed(() => files.value.length > 0 && !analyzing.value)

async function runBatch() {
  if (!files.value.length) return
  analyzing.value = true
  errorMsg.value = ''
  result.value = null
  try {
    result.value = await analyzeSequencingBatch(files.value, excelFile.value, minQ.value)
  } catch (e: any) {
    const detail = e.response?.data?.detail
    if (typeof detail === 'string') errorMsg.value = detail
    else if (Array.isArray(detail)) errorMsg.value = detail.map((d: any) => d.msg || JSON.stringify(d)).join('；')
    else errorMsg.value = e.message || '批量分析失败'
  } finally {
    analyzing.value = false
  }
}

// ==================== 结果展示 ====================
interface Badge { label: string; cls: string }

function itemBadge(it: SequencingBatchItem): Badge {
  if (it.status === 'failed') return { label: '失败', cls: 'bad' }
  if (it.status === 'no_reference') return { label: '缺图谱', cls: 'warn' }
  if (it.status === 'no_reads') return { label: '缺 reads', cls: 'warn' }
  if (it.status === 'not_found') return { label: '无文件', cls: 'idle' }
  if (it.conclusion.startsWith('不合格')) return { label: '不合格', cls: 'bad' }
  if (it.conclusion.startsWith('合格')) return { label: '合格', cls: 'ok' }
  return { label: '已分析', cls: 'ok' }
}

const summary = computed(() => {
  const items = result.value?.items ?? []
  const n = (cls: string) => items.filter((it) => itemBadge(it).cls === cls).length
  return { ok: n('ok'), bad: n('bad'), warn: n('warn'), idle: n('idle') }
})

function viewDetail(it: SequencingBatchItem) {
  if (!it.analysis_id) return
  router.push({ path: '/sequencing', query: { history: it.analysis_id } })
}
</script>

<template>
  <div class="batch-seq-page">
    <div class="page-header">
      <h1>🧬 批量测序分析</h1>
      <p class="subtitle">
        测序公司交付的「信息表 + 一批 .ab1/.dna」整个文件夹一次上传，按质粒归组逐个分析，
        一句话结论速览；单样品的峰图级核对在
        <router-link to="/sequencing" class="inline-link">测序分析</router-link> 页
      </p>
    </div>

    <!-- ① 上传 -->
    <div class="panel-card">
      <div class="ref-header">
        <span class="step-no">①</span>
        <h2>导入测序结果文件夹</h2>
      </div>
      <div
        class="upload-area"
        :class="{ drag: dragOver }"
        @dragover.prevent="dragOver = true"
        @dragleave="dragOver = false"
        @drop.prevent="onDrop"
      >
        <p class="upload-hint">
          拖入<b>整个交付文件夹</b>（.ab1 测序文件 + .dna/.gb/.fasta 图谱，可多质粒混在一起）
        </p>
        <div class="upload-btns">
          <label class="upload-btn">
            选择文件
            <input type="file" multiple @change="onFilePick" hidden />
          </label>
          <label class="upload-btn secondary">
            选择文件夹
            <input type="file" multiple webkitdirectory @change="onFilePick" hidden />
          </label>
          <label class="upload-btn secondary">
            {{ excelFile ? '更换信息表' : '信息表（可选）' }}
            <input type="file" accept=".xlsx" @change="onExcelPick" hidden />
          </label>
        </div>
        <p class="excel-note">
          信息表（.xlsx，表头含「质粒名称/测序引物/测序结果」，引物列填测序文件名）可精确定匹归组；
          不上传时按「测序文件名包含图谱文件名」自动归组
        </p>

        <div v-if="files.length || excelFile" class="staged">
          <p class="staged-line">
            <span>测序/图谱文件 × {{ files.length }}</span>
            <span v-if="excelFile" class="file-chip ref">📊 {{ excelFile.name }}<button class="file-remove" title="移除信息表" @click="clearExcel">×</button></span>
            <button class="clear-btn" title="清空全部文件" @click="clearFiles">一键清除</button>
          </p>
        </div>
        <p v-else class="stage-empty">尚未选择文件</p>
        <p v-if="errorMsg" class="error-msg">{{ errorMsg }}</p>
      </div>

      <details class="advanced">
        <summary>高级参数</summary>
        <label>末端修剪 Q 阈值（0-60，默认 20）<input type="number" v-model.number="minQ" min="0" max="60" @change="normalizeMinQ" /></label>
      </details>
      <button class="analyze-btn" :disabled="!canAnalyze" @click="runBatch">
        {{ analyzing ? '批量分析中…（逐质粒运行，请稍候）' : '开始批量分析' }}
      </button>
    </div>

    <!-- ② 结果 -->
    <template v-if="result">
      <div class="panel-card">
        <div class="ref-header">
          <span class="step-no">②</span>
          <h2>结论总览</h2>
          <span class="mode-chip">{{ result.excel_mode ? '按信息表归组' : '按文件名归组' }}</span>
        </div>
        <p class="summary-line">
          共 {{ result.items.length }} 个质粒：
          <span class="count ok">合格 {{ summary.ok }}</span> ·
          <span class="count bad">不合格/失败 {{ summary.bad }}</span> ·
          <span class="count warn">待补 {{ summary.warn }}</span>
        </p>
        <table class="seq-table" v-if="result.items.length">
          <thead>
            <tr><th>质粒</th><th>图谱</th><th>reads</th><th>覆盖</th><th>确证差异</th><th>一句话结论</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="it in result.items" :key="it.plasmid">
              <td class="plasmid-cell">
                <span class="badge" :class="itemBadge(it).cls">{{ itemBadge(it).label }}</span>
                {{ it.plasmid }}
              </td>
              <td class="mono">{{ it.reference_name || '—' }}</td>
              <td>{{ it.read_count }}</td>
              <td>{{ it.coverage_percent != null ? it.coverage_percent + '%' : '—' }}</td>
              <td>
                {{ it.variant_count }}
                <span v-if="it.pending_count" class="pending-note" title="低置信（疑似测序噪声）未计入结论">+{{ it.pending_count }} 待复核</span>
              </td>
              <td class="conclusion-cell">{{ it.conclusion }}</td>
              <td>
                <button v-if="it.analysis_id" class="mini-btn" @click="viewDetail(it)">查看详情</button>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>

        <div v-if="result.unmatched.length" class="note-box">
          <p class="note-title">未匹配文件（{{ result.unmatched.length }} 个，未参与分析）</p>
          <p v-for="u in result.unmatched" :key="u.filename" class="note-line">
            {{ u.filename }} — {{ u.reason }}
          </p>
        </div>
        <p v-if="result.ignored_files.length" class="note-line muted">
          已忽略无关文件：{{ result.ignored_files.slice(0, 5).join('、') }}{{ result.ignored_files.length > 5 ? ' 等' : '' }}
        </p>
        <p class="hint">
          「查看详情」跳转到测序分析页逐碱基核对（比对校验 / 峰图 / 共识序列）；
          需要整理副本与 Excel 回填结论时，可离线运行
          <code>scripts/batch_sequencing_report.py</code>
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.batch-seq-page {
  max-width: 1100px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.page-header h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
.subtitle { color: var(--text-secondary, #888); font-size: 0.9rem; }
.inline-link { color: var(--primary-color, #4E79C7); }

.panel-card {
  background: #fff;
  border: 1px solid var(--border-color, #E5E7EB);
  border-radius: 12px;
  padding: 1.25rem 1.5rem;
}

.ref-header { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 1rem; }
.ref-header h2 { font-size: 1.05rem; margin: 0; }
.step-no { color: var(--primary-color, #4E79C7); font-weight: 700; }
.mode-chip {
  margin-left: auto; font-size: 0.75rem; padding: 2px 10px; border-radius: 999px;
  background: #EDF2EE; color: #5E7A64;
}

.upload-area {
  border: 2px dashed var(--border-color, #ddd);
  border-radius: 10px;
  padding: 1.5rem;
  text-align: center;
}
.upload-area.drag { border-color: var(--primary-color, #45B7D1); background: rgba(69,183,209,0.05); }
.upload-hint { font-size: 0.9rem; margin-bottom: 0.75rem; }
.upload-btns { display: flex; gap: 0.6rem; justify-content: center; margin-bottom: 0.6rem; flex-wrap: wrap; }
.upload-btn {
  display: inline-block; padding: 0.5rem 1.2rem; background: var(--primary-color, #45B7D1);
  color: #fff; border-radius: 6px; cursor: pointer; font-size: 0.9rem;
}
.upload-btn.secondary { background: var(--text-secondary, #8aa0b4); }
.excel-note { font-size: 0.78rem; color: var(--text-secondary, #999); margin: 0 0 0.5rem; }

.staged { margin-top: 0.5rem; }
.staged-line { display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem; justify-content: center; font-size: 0.85rem; }
.file-chip {
  background: var(--bg-secondary, #f5f5f5); padding: 0.25rem 0.6rem; border-radius: 999px;
  font-size: 0.8rem; display: inline-flex; align-items: center; gap: 0.35rem;
}
.file-chip.ref { background: #F0FAF2; border: 1px solid #BFE5C8; }
.file-remove { border: none; background: none; cursor: pointer; font-size: 1rem; color: #c00; }
.clear-btn {
  border: 1px solid var(--border-color, #ddd); background: #fff; color: #c0392b;
  border-radius: 999px; font-size: 0.72rem; padding: 0.1rem 0.55rem; cursor: pointer;
}
.clear-btn:hover { background: #FDE8E8; border-color: #E8A5A5; }
.stage-empty { font-size: 0.85rem; color: var(--text-secondary, #999); margin: 0.25rem 0 0; }
.error-msg { color: #c0392b; font-size: 0.85rem; margin-top: 0.5rem; }

.advanced { margin-top: 0.75rem; font-size: 0.85rem; text-align: left; display: inline-block; }
.advanced label { display: block; margin: 0.35rem 0; }
.analyze-btn {
  display: block; margin: 1rem auto 0; padding: 0.6rem 2rem; border: none;
  background: #2E9E44; color: #fff; border-radius: 6px; cursor: pointer; font-size: 1rem;
}
.analyze-btn:disabled { background: #aaa; cursor: not-allowed; }

.summary-line { font-size: 0.95rem; margin: 0 0 0.75rem; }
.count.ok { color: #2E9E44; font-weight: 600; }
.count.bad { color: #C0392B; font-weight: 600; }
.count.warn { color: #9A6D00; font-weight: 600; }

.seq-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.seq-table th, .seq-table td { padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border-color, #eee); text-align: left; }
.seq-table th { background: var(--bg-secondary, #f7f7f7); }
.plasmid-cell { font-weight: 600; white-space: nowrap; }
.mono { font-family: Consolas, monospace; }
.conclusion-cell { max-width: 420px; }
.pending-note { font-size: 0.72rem; color: #9A6D00; }

.badge {
  display: inline-block; min-width: 2.4rem; text-align: center; padding: 1px 8px;
  border-radius: 999px; font-size: 0.72rem; font-weight: 400; margin-right: 4px; vertical-align: 1px;
}
.badge.ok { background: #E8F6EC; color: #2E9E44; }
.badge.bad { background: #FDE8E8; color: #C0392B; }
.badge.warn { background: #FCF3DC; color: #9A6D00; }
.badge.idle { background: #EEE; color: #777; }

.note-box { background: var(--bg-secondary, #f9f9f9); border-radius: 8px; padding: 0.6rem 0.9rem; margin-top: 0.75rem; }
.note-title { font-size: 0.8rem; font-weight: 600; color: #9A6D00; margin: 0 0 0.25rem; }
.note-line { font-size: 0.78rem; color: #888; margin: 0.1rem 0; }
.note-line.muted { margin-top: 0.5rem; }

.hint { color: #999; font-size: 0.8rem; margin-top: 0.75rem; }
.hint code { background: var(--bg-secondary, #f5f5f5); padding: 1px 5px; border-radius: 4px; }
</style>
