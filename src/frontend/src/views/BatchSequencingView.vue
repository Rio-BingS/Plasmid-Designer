<script setup lang="ts">
/**
 * 批量测序分析 — 独立入口（单样品分析见 /sequencing）
 * 测序公司交付常为「Excel 信息表 + 一批 .ab1/.dna」：整个文件夹一次上传，
 * 按质粒归组（信息表引物列/质粒名，缺省时按图谱文件名包含关系）逐个跑
 * 全自动管线，一句话结论速览；详情深链到 /sequencing?history=<analysis_id>
 */
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { analyzeSequencingBatch, downloadBatchSequencingReport, formatApiError, type SequencingBatchItem, type SequencingBatchResult } from '@/api'

const router = useRouter()

// ==================== 文件导入（支持整个文件夹拖入） ====================
const files = ref<File[]>([])
const excelFile = ref<File | null>(null)
const ignoredNames = ref<string[]>([])
const skippedNames = ref<string[]>([])
const errorMsg = ref('')
const dragOver = ref(false)

function fileExt(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1).toLowerCase() : ''
}

// Excel/WPS 打开表格时会留下 ~$ 开头的锁文件（按名称排序常排在正主前面，
// 选择整个文件夹会被一起选中，还可能因被占用而读不了）；连同系统垃圾文件一并跳过
const JUNK_NAMES = new Set(['desktop.ini', 'thumbs.db', '.ds_store'])

function isTempOrJunk(name: string): boolean {
  return name.startsWith('~$') || JUNK_NAMES.has(name.toLowerCase())
}

function addFiles(list: File[] | FileList | null | undefined) {
  if (!list) return
  ignoredNames.value = []
  skippedNames.value = []
  errorMsg.value = ''
  for (const f of Array.from(list)) {
    if (isTempOrJunk(f.name)) {
      skippedNames.value.push(f.name)
      continue
    }
    if (fileExt(f.name) === 'xlsx') {
      if (!excelFile.value) excelFile.value = f
      continue
    }
    files.value.push(f)
  }
}

function clearFiles() { files.value = []; skippedNames.value = [] }
function clearExcel() { excelFile.value = null; skippedNames.value = [] }

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
  if (f) {
    if (isTempOrJunk(f.name)) {
      skippedNames.value = [f.name]
      errorMsg.value = '所选信息表是 Excel 临时文件（~$ 开头），请选择正式的信息表 .xlsx'
    } else {
      excelFile.value = f
    }
  }
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

// Cloudflare 免费版有 100MB 请求体硬上限，超出时连接会被直接掐断（浏览器只看到
// Network Error），留余量在上传前拦截
const MAX_UPLOAD_BYTES = 95 * 1024 * 1024

const stagedSizeMB = computed(() => {
  const bytes = [...files.value, ...(excelFile.value ? [excelFile.value] : [])]
    .reduce((s, f) => s + f.size, 0)
  return (bytes / 1024 / 1024).toFixed(1)
})

// 文件被占用时（如正被 Excel 打开的表格）浏览器读不出来，XHR 会在发送阶段整体
// 失败且 axios 只报笼统的 Network Error——上传前逐个试读 1 字节，把问题文件挑
// 出来给明确提示
async function fileReadable(f: File): Promise<boolean> {
  try {
    await f.slice(0, 1).text()
    return true
  } catch {
    return false
  }
}

async function runBatch() {
  if (!files.value.length) return
  const staged = [...files.value, ...(excelFile.value ? [excelFile.value] : [])]
  const totalBytes = staged.reduce((s, f) => s + f.size, 0)
  if (totalBytes > MAX_UPLOAD_BYTES) {
    errorMsg.value = `所选文件总体积 ${stagedSizeMB.value} MB，超过免费版 Cloudflare 的 100 MB 请求体上限，请删掉部分文件分批上传`
    return
  }
  analyzing.value = true
  errorMsg.value = ''
  result.value = null
  try {
    const unreadable: string[] = []
    for (const f of staged) {
      if (!(await fileReadable(f))) unreadable.push(f.name)
    }
    if (unreadable.length) {
      errorMsg.value = `这些文件无法读取（多半正被 Excel/WPS 打开占用）：${unreadable.join('、')}——请关闭占用程序后重试`
      return
    }
    result.value = await analyzeSequencingBatch(files.value, excelFile.value, minQ.value)
  } catch (e: any) {
    errorMsg.value = formatApiError(e, '批量分析失败')
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

// 克隆模式：信息表带「克隆号」列，同一质粒的每个克隆独立成行
const cloneMode = computed(() => (result.value?.items ?? []).some((it) => !!it.clone))
const plasmidCount = computed(() => new Set((result.value?.items ?? []).map((it) => it.plasmid)).size)

function viewDetail(it: SequencingBatchItem) {
  if (!it.analysis_id) return
  router.push({ path: '/sequencing', query: { history: it.analysis_id } })
}

// 整理包：服务器按上传原始字节打包（归档副本 + 各组分析报告 + 回填信息表），
// 与分析记录一样 15 分钟后自动清理
const downloading = ref(false)
async function downloadReport() {
  if (!result.value?.batch_id || downloading.value) return
  downloading.value = true
  try {
    await downloadBatchSequencingReport(result.value.batch_id)
  } catch (e: any) {
    errorMsg.value = formatApiError(e, '整理包下载失败')
  } finally {
    downloading.value = false
  }
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
          信息表（.xlsx，表头含「质粒名称/测序引物/测序结果」，引物列填测序文件名，多个用中英文分号分隔均可）可精确定匹归组；
          带「克隆号」列时按克隆分组——一个质粒多个克隆每行一个、独立分析（ab1 文件名 = 克隆号-引物），
          质粒名称写全名或两段式名称的任一段（如 "123-1 AB2C" 只写 "123-1" 或 "AB2C"）均可识别；
          不上传信息表时按「测序文件名包含图谱文件名」自动归组
        </p>

        <div v-if="files.length || excelFile" class="staged">
          <p class="staged-line">
            <span>测序/图谱文件 × {{ files.length }} · 共 {{ stagedSizeMB }} MB</span>
            <span v-if="excelFile" class="file-chip ref">📊 {{ excelFile.name }}<button class="file-remove" title="移除信息表" @click="clearExcel">×</button></span>
            <button class="clear-btn" title="清空全部文件" @click="clearFiles">一键清除</button>
          </p>
          <p v-if="skippedNames.length" class="note-line muted">
            已自动跳过临时/系统文件：{{ skippedNames.slice(0, 5).join('、') }}{{ skippedNames.length > 5 ? ' 等' : '' }}
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
        {{ analyzing ? '批量分析中…（逐样品运行，请稍候）' : '开始批量分析' }}
      </button>
    </div>

    <!-- ② 结果 -->
    <template v-if="result">
      <div class="panel-card">
        <div class="ref-header">
          <span class="step-no">②</span>
          <h2>结论总览</h2>
          <span class="mode-chip">
            {{ result.clone_mode ? '信息表 · 克隆模式' : result.excel_mode ? '按信息表归组' : '按文件名归组' }}
          </span>
        </div>
        <p class="summary-line">
          <template v-if="cloneMode">
            共 {{ plasmidCount }} 个质粒 / {{ result.items.length }} 个克隆：
          </template>
          <template v-else>共 {{ result.items.length }} 个质粒：</template>
          <span class="count ok">合格 {{ summary.ok }}</span> ·
          <span class="count bad">不合格/失败 {{ summary.bad }}</span> ·
          <span class="count warn">待补 {{ summary.warn }}</span>
        </p>
        <p class="report-row">
          <button v-if="result.report_ready" class="report-btn" :disabled="downloading" @click="downloadReport">
            {{ downloading ? '整理包准备中…' : '⬇ 下载整理包（按质粒归档 + 分析报告 + 回填信息表）' }}
          </button>
          <span class="report-note">整理包内是原始文件副本（未改动）+ 各组分析报告 + 整理清单；与分析记录一样 15 分钟后自动清理</span>
        </p>
        <table class="seq-table" v-if="result.items.length">
          <thead>
            <tr><th>质粒</th><th v-if="cloneMode">克隆</th><th>图谱</th><th>reads</th><th>覆盖</th><th>确证差异</th><th>一句话结论</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="(it, i) in result.items" :key="i">
              <td class="plasmid-cell">
                <span class="badge" :class="itemBadge(it).cls">{{ itemBadge(it).label }}</span>
                {{ it.plasmid }}
              </td>
              <td v-if="cloneMode" class="mono">{{ it.clone }}</td>
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
          本地大批量处理仍可离线运行 <code>scripts/batch_sequencing_report.py</code>
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

.report-row { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; margin: 0 0 0.9rem; }
.report-btn {
  padding: 0.45rem 1.1rem; border: none; background: var(--primary-color, #4E79C7);
  color: #fff; border-radius: 6px; cursor: pointer; font-size: 0.88rem;
}
.report-btn:disabled { background: #aaa; cursor: not-allowed; }
.report-note { font-size: 0.76rem; color: var(--text-secondary, #999); }

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
