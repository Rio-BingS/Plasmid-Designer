<script setup lang="ts">
/**
 * 独立图谱页 /map：拖放/选择文件、粘贴序列 → POST /api/map/parse，或输入 NCBI
 * accession → POST /api/map/import/ncbi → 可编辑的 MapViewer。
 * 文档放 sessionStorage，刷新不丢；示例按钮载入内置载体 pUC19。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { EnzymeSet, MapDocument, ParseOptions, Topology } from '@/map/types'
import {
  checkMapFile, describeMapError, fileExt, getEnzymeSets, getVectorMap, importNcbi, MAP_ACCEPT, MAP_TEXT_EXTS,
  MAP_UPLOAD_LIMIT_MB, NCBI_ACCESSION_RE, normalizeAccession, parseMapFile, parseMapText,
} from '@/map/api'
import { loadMapSession, saveMapSession } from '@/map/session'
import MapViewer from '@/map/components/MapViewer.vue'

const PAGE_TITLE = '质粒图谱 - Plasmid Designer'
const EXAMPLE_VECTOR = 'pUC19'

const doc = ref<MapDocument | null>(loadMapSession())
const showInput = ref(!doc.value)
const mode = ref<'file' | 'paste' | 'ncbi'>('file')
const loading = ref(false)
const error = ref('')
const notSaved = ref(false)

// 选项
const annotate = ref<'default' | 'on' | 'off'>('default')
const enzymeSet = ref('common')
const enzymeSets = ref<EnzymeSet[]>([
  { id: 'common', name: '常用酶', description: '', enzymes: [] },
  { id: 'unique6', name: '单切点 6+ 碱基酶', description: '', enzymes: [] },
  { id: 'commercial', name: '全部商品化酶', description: '', enzymes: [] },
  { id: 'golden_gate', name: 'Golden Gate', description: '', enzymes: [] },
])
function options(): ParseOptions {
  const o: ParseOptions = { enzyme_set: enzymeSet.value }
  if (annotate.value !== 'default') o.annotate = annotate.value === 'on'
  return o
}

// 粘贴
const pasteName = ref('')
const pasteTopology = ref<Topology>('circular')
const pasteText = ref('')
const pasteLooksGenbank = computed(() => /^\s*LOCUS\b/.test(pasteText.value))

// NCBI accession
const accession = ref('')
const accessionValid = computed(() => NCBI_ACCESSION_RE.test(normalizeAccession(accession.value)))

// 拖放
const dragging = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

let prevTitle = ''
onMounted(async () => {
  prevTitle = document.title
  document.title = PAGE_TITLE
  try {
    const sets = await getEnzymeSets()
    if (Array.isArray(sets) && sets.length) enzymeSets.value = sets
  } catch { /* 保留内置列表 */ }
})
onBeforeUnmount(() => { if (prevTitle) document.title = prevTitle })

watch(doc, (d) => { notSaved.value = !!d && !saveMapSession(d) })

function setDoc(d: MapDocument) {
  doc.value = d
  showInput.value = false
  error.value = ''
}

async function run(task: () => Promise<MapDocument>) {
  loading.value = true
  error.value = ''
  try {
    setDoc(await task())
  } catch (e) {
    error.value = describeMapError(e)
  } finally {
    loading.value = false
  }
}

async function openFile(file: File | null | undefined) {
  if (!file) return
  const bad = checkMapFile(file)
  if (bad) { error.value = bad; return }
  if (MAP_TEXT_EXTS.includes(fileExt(file.name))) {
    const name = file.name.replace(/\.[^.]+$/, '')
    await run(async () => parseMapText(await file.text(), { name }, options()))
  } else {
    await run(() => parseMapFile(file, options()))
  }
}

function onPick(e: Event) {
  const input = e.target as HTMLInputElement
  openFile(input.files?.[0])
  input.value = ''
}
function onDrop(e: DragEvent) {
  dragging.value = false
  openFile(e.dataTransfer?.files?.[0])
}

async function openPaste() {
  const text = pasteText.value.trim()
  if (!text) { error.value = '请粘贴序列、FASTA 或 GenBank 文本'; return }
  const extra: { name?: string; topology?: Topology } = {}
  if (pasteName.value.trim()) extra.name = pasteName.value.trim()
  // GenBank 文本自带拓扑（LOCUS 行），不覆盖
  if (!pasteLooksGenbank.value) extra.topology = pasteTopology.value
  await run(() => parseMapText(text, extra, options()))
}

async function openNcbi() {
  const acc = normalizeAccession(accession.value)
  if (!acc) { error.value = '请输入 NCBI accession，如 L09137 或 M77789.1'; return }
  if (!NCBI_ACCESSION_RE.test(acc)) {
    error.value = `accession 格式无效：${acc}（示例：L09137、M77789.1、NC_005213.1）`
    return
  }
  const o = options()
  await run(() => importNcbi({ accession: acc, ...o }))
}

async function openExample() {
  await run(() => getVectorMap(EXAMPLE_VECTOR, { enzyme_set: enzymeSet.value }))
}

function onDocChange(d: MapDocument) { doc.value = d }

function closeDoc() {
  doc.value = null
  saveMapSession(null)
  showInput.value = true
}
</script>

<template>
  <div class="map-page">
    <header class="map-head">
      <div>
        <h1>质粒图谱</h1>
        <p class="subtitle">打开 GenBank / FASTA / SnapGene 文件或粘贴序列，查看环形/线性/序列图谱，编辑注释、模拟酶切</p>
      </div>
      <div v-if="doc" class="map-head-actions">
        <button type="button" class="btn btn-secondary btn-sm" @click="showInput = !showInput">{{ showInput ? '收起' : '打开其他序列' }}</button>
        <button type="button" class="btn btn-secondary btn-sm map-close" @click="closeDoc">关闭当前图谱</button>
      </div>
    </header>

    <section v-if="showInput" class="card map-input">
      <div class="map-tabs" role="tablist">
        <button type="button" role="tab" :class="['map-tab', { active: mode === 'file' }]" :aria-selected="mode === 'file'" @click="mode = 'file'">上传文件</button>
        <button type="button" role="tab" :class="['map-tab', { active: mode === 'paste' }]" :aria-selected="mode === 'paste'" @click="mode = 'paste'">粘贴序列</button>
        <button type="button" role="tab" :class="['map-tab', { active: mode === 'ncbi' }]" :aria-selected="mode === 'ncbi'" @click="mode = 'ncbi'">NCBI 导入</button>
      </div>

      <div v-if="mode === 'file'"
           :class="['map-drop', { dragging }]" role="button" tabindex="0" aria-label="拖放或选择序列文件"
           @click="fileInput?.click()" @keydown.enter.prevent="fileInput?.click()" @keydown.space.prevent="fileInput?.click()"
           @dragover.prevent="dragging = true" @dragenter.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="onDrop">
        <input ref="fileInput" class="map-file" type="file" :accept="MAP_ACCEPT" @change="onPick" />
        <p class="map-drop-main">将文件拖到这里，或<span class="map-link">点击选择</span></p>
        <p class="map-drop-sub">GenBank（.gb/.gbk/.genbank）、FASTA（.fa/.fasta）、SnapGene（.dna）、纯文本（.txt）· 不超过 {{ MAP_UPLOAD_LIMIT_MB }} MB</p>
      </div>

      <div v-else-if="mode === 'ncbi'" class="map-ncbi">
        <div class="map-row">
          <input v-model="accession" class="form-input map-accession" type="text" placeholder="GenBank / RefSeq accession，如 L09137、M77789.1"
                 maxlength="40" spellcheck="false" aria-label="NCBI accession" @keydown.enter.prevent="openNcbi" />
          <button type="button" class="btn btn-primary map-ncbi-open" :disabled="loading || !accession.trim()" @click="openNcbi">从 NCBI 打开</button>
        </div>
        <p class="map-drop-sub">经 NCBI E-utilities 获取完整 GenBank 记录（含注释）；序列不超过 300 kb。
          <span v-if="accession.trim() && !accessionValid" class="map-ncbi-hint">格式看起来不对：应为字母 + 数字，可带版本号（.1）</span></p>
      </div>

      <div v-else class="map-paste">
        <div class="map-row">
          <input v-model="pasteName" class="form-input map-name" type="text" placeholder="名称（可选）" maxlength="80" aria-label="序列名称" />
          <div class="map-topology" role="radiogroup" aria-label="拓扑" :title="pasteLooksGenbank ? 'GenBank 文本按 LOCUS 行的拓扑' : ''">
            <label><input v-model="pasteTopology" type="radio" value="circular" :disabled="pasteLooksGenbank" /> 环状</label>
            <label><input v-model="pasteTopology" type="radio" value="linear" :disabled="pasteLooksGenbank" /> 线性</label>
          </div>
        </div>
        <textarea v-model="pasteText" class="form-input map-text" rows="7" spellcheck="false"
                  placeholder="粘贴纯序列、FASTA 或完整的 GenBank 文本" aria-label="序列文本"></textarea>
        <button type="button" class="btn btn-primary map-open" :disabled="loading || !pasteText.trim()" @click="openPaste">打开</button>
      </div>

      <div class="map-options">
        <label>自动注释
          <select v-model="annotate" class="map-select" aria-label="自动注释">
            <option value="default">自动（文件无注释时）</option>
            <option value="on">开启</option>
            <option value="off">关闭</option>
          </select>
        </label>
        <label>酶组
          <select v-model="enzymeSet" class="map-select" aria-label="酶组">
            <option v-for="s in enzymeSets" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </label>
        <button type="button" class="btn btn-secondary btn-sm map-example" :disabled="loading" @click="openExample">示例：{{ EXAMPLE_VECTOR }}</button>
        <span v-if="loading" class="map-muted">解析中…</span>
      </div>
      <p v-if="error" class="map-error" role="alert">{{ error }}</p>
    </section>
    <p v-else-if="error" class="map-error" role="alert">{{ error }}</p>

    <p v-if="notSaved" class="map-muted map-notsaved">序列较大，未能暂存到本标签页，刷新后需重新打开。</p>

    <MapViewer v-if="doc" :doc="doc" editable @doc-change="onDocChange" />
    <div v-else-if="!showInput" class="map-empty">尚未打开序列</div>
  </div>
</template>

<style scoped>
.map-page { display: flex; flex-direction: column; gap: 1rem; }
.map-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 1rem; flex-wrap: wrap; }
h1 { font-size: 2rem; margin-bottom: 0.25rem; }
.subtitle { color: var(--text-secondary); }
.map-head-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.btn-sm { padding: 0.45rem 0.9rem; font-size: 0.875rem; }
.map-input { display: flex; flex-direction: column; gap: 0.9rem; }
.map-tabs { display: flex; gap: 0.25rem; border-bottom: 1px solid var(--border-color); }
.map-tab { padding: 0.45rem 1rem; border: none; background: none; color: var(--text-secondary); border-bottom: 2px solid transparent; font-size: 0.95rem; }
.map-tab.active { color: var(--primary-color); border-bottom-color: var(--primary-color); font-weight: 600; }
.map-drop {
  border: 2px dashed var(--border-color); border-radius: 10px; padding: 2rem 1rem; text-align: center; cursor: pointer;
  background: var(--bg-secondary); transition: border-color 0.15s, background 0.15s;
}
.map-drop.dragging, .map-drop:hover, .map-drop:focus-visible { border-color: var(--primary-color); background: #eef2ff; outline: none; }
.map-file { display: none; }
.map-drop-main { font-size: 1rem; }
.map-drop-sub { margin-top: 0.35rem; color: var(--text-secondary); font-size: 0.85rem; }
.map-link { color: var(--primary-color); text-decoration: underline; margin-left: 0.2rem; }
.map-paste { display: flex; flex-direction: column; gap: 0.6rem; }
.map-row { display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; }
.map-name { flex: 1; min-width: 180px; padding: 0.5rem 0.75rem; }
.map-topology { display: flex; gap: 0.75rem; }
.map-topology label { display: inline-flex; gap: 0.25rem; align-items: center; }
.map-text { font-family: Consolas, Menlo, 'Courier New', monospace; font-size: 0.85rem; resize: vertical; }
.map-open { align-self: flex-start; }
.map-ncbi { display: flex; flex-direction: column; gap: 0.4rem; }
.map-accession { flex: 1; min-width: 200px; padding: 0.5rem 0.75rem; font-family: Consolas, Menlo, 'Courier New', monospace; }
.map-ncbi-hint { color: var(--warning-color); margin-left: 0.4rem; }
.map-options { display: flex; gap: 0.6rem 1.2rem; align-items: center; flex-wrap: wrap; font-size: 0.9rem; }
.map-options label { display: inline-flex; gap: 0.4rem; align-items: center; }
.map-select { padding: 0.35rem 0.5rem; border: 1px solid var(--border-color); border-radius: 6px; background: var(--bg-primary); }
.map-error { color: var(--error-color); background: #fef2f2; border-radius: 6px; padding: 0.5rem 0.75rem; font-size: 0.9rem; }
.map-muted { color: var(--text-secondary); font-size: 0.85rem; }
.map-empty { color: var(--text-secondary); text-align: center; padding: 2rem; }
@media (max-width: 640px) {
  h1 { font-size: 1.5rem; }
  .map-drop { padding: 1.25rem 0.75rem; }
  .map-head-actions { width: 100%; }
  .map-head-actions .btn { flex: 1; }
}
</style>
