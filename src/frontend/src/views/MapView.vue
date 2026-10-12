<script setup lang="ts">
/**
 * 独立图谱页 /map：拖放/选择文件、粘贴序列 → POST /api/map/parse，或输入 NCBI
 * accession → POST /api/map/import/ncbi → 可编辑的 MapViewer。
 * 文档放 sessionStorage，刷新不丢；示例按钮载入内置载体 pUC19。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { EnzymeSet, MapDocument, ParseOptions, Topology } from '@/map/types'
import {
  checkMapFile, describeMapError, ENSEMBL_ID_RE, fileExt, getEnzymeSets, getOnlineSnapgeneIndex,
  getOnlineSnapgeneSets, getOnlineSources, getVectorMap, importNcbi, importOnlineEnsembl,
  importOnlineSnapgene, MAP_ACCEPT, MAP_TEXT_EXTS, MAP_UPLOAD_LIMIT_MB, NCBI_ACCESSION_RE,
  normalizeAccession, parseMapFile, parseMapText,
} from '@/map/api'
import type { OnlineSourceInfo, SnapgenePlasmid, SnapgeneSet } from '@/map/types'
import { loadMapSession, saveMapSession } from '@/map/session'
import MapViewer from '@/map/components/MapViewer.vue'
import { saveMapAsVector } from '@/api'
import { useIsAdmin } from '@/composables/useIsAdmin'
import { onTabKeydown } from '@/utils/tabKeys'

const PAGE_TITLE = '质粒图谱 - Plasmid Designer'
const EXAMPLE_VECTOR = 'pUC19'

const doc = ref<MapDocument | null>(loadMapSession())
const showInput = ref(!doc.value)
const mode = ref<'file' | 'paste' | 'ncbi' | 'online'>('file')
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

// 在线导入（SnapGene 在线库 / Ensembl）：与载体库页共用后端缓存与许可文案
const onlineSource = ref<'snapgene' | 'ensembl'>('snapgene')
const sgSets = ref<SnapgeneSet[]>([])
const sgSetId = ref('')
const sgRows = ref<SnapgenePlasmid[]>([])
const sgLoading = ref(false)
const sgError = ref('')
const ensemblId = ref('')
const ensemblType = ref<'genomic' | 'cds' | 'cdna'>('cds')
const onlineSources = ref<OnlineSourceInfo[]>([])
// 许可文案随选中来源切换（SnapGene 与 Ensembl 条款不同）
const onlineLicense = computed(
  () => onlineSources.value.find((s) => s.id === onlineSource.value)?.license || '')
const ensemblValid = computed(() => ENSEMBL_ID_RE.test(ensemblId.value.trim()))

async function ensureOnlineMeta() {
  if (onlineSources.value.length === 0) {
    try {
      onlineSources.value = (await getOnlineSources()).sources || []
    } catch { /* 许可提示拿不到不阻断导入 */ }
  }
  if (sgSets.value.length === 0 && !sgLoading.value) await loadSnapgeneSets()
}

async function loadSnapgeneSets() {
  sgLoading.value = true
  sgError.value = ''
  try {
    const res = await getOnlineSnapgeneSets()
    sgSets.value = res.sets || []
    if (!sgSetId.value && sgSets.value.length) sgSetId.value = sgSets.value[0].set_id
    if (sgSetId.value) await loadSnapgeneIndex()
  } catch (e) {
    sgError.value = describeMapError(e)
  } finally {
    sgLoading.value = false
  }
}

// 集合索引请求号：快速切换集合时只认最后一次请求，乱序返回的旧集合不覆盖列表
let sgIndexGen = 0
async function loadSnapgeneIndex() {
  if (!sgSetId.value) return
  const gen = ++sgIndexGen
  sgLoading.value = true
  sgError.value = ''
  sgRows.value = []
  try {
    const rows = (await getOnlineSnapgeneIndex(sgSetId.value)).plasmids || []
    if (gen === sgIndexGen) sgRows.value = rows
  } catch (e) {
    if (gen === sgIndexGen) sgError.value = describeMapError(e)
  } finally {
    if (gen === sgIndexGen) sgLoading.value = false
  }
}

async function openOnlineSnapgene(row: SnapgenePlasmid) {
  await run(() => importOnlineSnapgene({ set_id: sgSetId.value, plasmid_id: row.plasmid_id, ...options() }))
}

async function openOnlineEnsembl() {
  const id = ensemblId.value.trim()
  if (!id) { error.value = '请输入 Ensembl ID，如 ENSG00000139618 或 ENST00000288602'; return }
  if (!ensemblValid.value) { error.value = `Ensembl ID 格式看起来不对：${id}`; return }
  await run(() => importOnlineEnsembl({ identifier: id, seq_type: ensemblType.value, ...options() }))
}

function openOnlineTab() {
  mode.value = 'online'
  ensureOnlineMeta()
}

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

// 解析请求号：解析中又拖入/选择了另一个文件时，只采用最后一次的结果（先发后到的不覆盖）
let runGen = 0
async function run(task: () => Promise<MapDocument>) {
  const gen = ++runGen
  loading.value = true
  error.value = ''
  try {
    const d = await task()
    if (gen === runGen) setDoc(d)
  } catch (e) {
    if (gen === runGen) error.value = describeMapError(e)
  } finally {
    if (gen === runGen) loading.value = false
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

// -------------------- 另存为载体（需管理员） --------------------
// 序列与注释直接取当前文档（用户可能已在图谱编辑器里改过），后端不再外呼，
// 保证存进载体库的与眼前这份是同一份数据。
const canSaveAsVector = useIsAdmin()
const saveOpen = ref(false)
const saveName = ref('')
const saveBusy = ref(false)
const saveMsg = ref('')
const saveFailed = ref(false)

function openSaveAsVector() {
  saveName.value = doc.value?.name || ''
  saveMsg.value = ''
  saveOpen.value = true
}

async function doSaveAsVector() {
  const d = doc.value
  if (!d) return
  const name = saveName.value.trim()
  if (!name) { saveMsg.value = '请填写载体名称'; saveFailed.value = true; return }
  saveBusy.value = true
  saveMsg.value = ''
  saveFailed.value = false
  try {
    const res = await saveMapAsVector({
      name,
      sequence: d.sequence,
      topology: d.topology,
      features: d.features.map((f) => ({
        name: f.name || f.type,
        type: f.type,
        start: f.start,
        end: f.end,
        strand: f.strand,
        notes: f.notes,
        color: f.color,
      })),
      source: d.meta?.source_format || 'map',
      description: `由图谱页保存（原文档：${d.name}，${d.length} bp）`,
    })
    saveMsg.value = `已存进载体库：${res.vector.name}（${res.vector.length} bp，${res.vector.features} 个特征）`
    saveOpen.value = false
  } catch (e) {
    saveFailed.value = true
    saveMsg.value = `保存失败：${describeMapError(e)}`
  } finally {
    saveBusy.value = false
  }
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
        <button v-if="canSaveAsVector" type="button" class="btn btn-secondary btn-sm" @click="openSaveAsVector">💾 另存为载体</button>
        <button type="button" class="btn btn-secondary btn-sm" @click="showInput = !showInput">{{ showInput ? '收起' : '打开其他序列' }}</button>
        <button type="button" class="btn btn-secondary btn-sm map-close" @click="closeDoc">关闭当前图谱</button>
      </div>
    </header>

    <div v-if="saveOpen && doc" class="card map-saveas">
      <div class="map-row">
        <input v-model="saveName" class="form-input map-name" type="text" maxlength="80"
               placeholder="载体名称，如 pUC19-insert" aria-label="载体名称" @keydown.enter.prevent="doSaveAsVector" />
        <button type="button" class="btn btn-primary" :disabled="saveBusy" @click="doSaveAsVector">
          {{ saveBusy ? '保存中…' : '存入载体库' }}
        </button>
        <button type="button" class="btn btn-secondary" @click="saveOpen = false">取消</button>
      </div>
      <p class="map-drop-sub">存入的是当前文档的序列与注释（{{ doc.length.toLocaleString() }} bp · {{ doc.features.length }} 个元件）；
        酶切位点/ORF 等分析结果不随载体库保存，需要时打开载体详情会重算。</p>
    </div>
    <p v-if="saveMsg" :class="['map-saveas-msg', saveFailed ? 'map-error' : 'map-muted']" role="status">{{ saveMsg }}</p>

    <section v-if="showInput" class="card map-input">
      <div class="map-tabs" role="tablist" @keydown="onTabKeydown">
        <button type="button" role="tab" :class="['map-tab', { active: mode === 'file' }]" :aria-selected="mode === 'file'" @click="mode = 'file'">上传文件</button>
        <button type="button" role="tab" :class="['map-tab', { active: mode === 'paste' }]" :aria-selected="mode === 'paste'" @click="mode = 'paste'">粘贴序列</button>
        <button type="button" role="tab" :class="['map-tab', { active: mode === 'ncbi' }]" :aria-selected="mode === 'ncbi'" @click="mode = 'ncbi'">NCBI 导入</button>
        <button type="button" role="tab" :class="['map-tab', { active: mode === 'online' }]" :aria-selected="mode === 'online'" @click="openOnlineTab">在线导入</button>
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

      <div v-else-if="mode === 'online'" class="map-online">
        <div class="map-online-src" role="radiogroup" aria-label="在线来源">
          <label><input v-model="onlineSource" type="radio" value="snapgene" /> SnapGene 在线库</label>
          <label><input v-model="onlineSource" type="radio" value="ensembl" /> Ensembl</label>
        </div>

        <template v-if="onlineSource === 'snapgene'">
          <div class="map-row">
            <select v-model="sgSetId" class="form-input map-online-set" aria-label="SnapGene 集合" @change="loadSnapgeneIndex">
              <option v-for="s in sgSets" :key="s.set_id" :value="s.set_id">{{ s.name }}（{{ s.set_id }}）</option>
            </select>
            <button type="button" class="btn btn-secondary" :disabled="sgLoading" @click="loadSnapgeneSets">
              {{ sgLoading ? '加载中…' : '刷新' }}
            </button>
          </div>
          <p v-if="sgError" class="map-online-error">{{ sgError }}</p>
          <p v-else-if="sgRows.length === 0" class="map-drop-sub">
            {{ sgLoading ? '正在获取集合索引…' : '该集合没有条目' }}
          </p>
          <ul v-else class="map-online-list">
            <li v-for="row in sgRows" :key="row.plasmid_id">
              <span class="map-online-id">{{ row.plasmid_id }}</span>
              <span class="map-online-name">{{ row.name }}</span>
              <button type="button" class="btn btn-primary btn-sm" :disabled="loading"
                      @click="openOnlineSnapgene(row)">打开</button>
            </li>
          </ul>
        </template>

        <template v-else>
          <div class="map-row">
            <input v-model="ensemblId" class="form-input map-accession" type="text"
                   placeholder="Ensembl ID，如 ENSG00000139618、ENST00000288602"
                   maxlength="64" spellcheck="false" aria-label="Ensembl ID"
                   @keydown.enter.prevent="openOnlineEnsembl" />
            <select v-model="ensemblType" class="form-input map-online-set" aria-label="序列类型">
              <option value="cds">CDS（编码序列）</option>
              <option value="cdna">cDNA</option>
              <option value="genomic">基因组区段</option>
            </select>
            <button type="button" class="btn btn-primary" :disabled="loading || !ensemblId.trim()"
                    @click="openOnlineEnsembl">打开</button>
          </div>
          <p class="map-drop-sub">导入的是线性片段（非质粒），蛋白序列不能作为图谱序列。
            <span v-if="ensemblId.trim() && !ensemblValid" class="map-ncbi-hint">格式看起来不对</span></p>
        </template>

        <p class="map-drop-sub map-online-license">{{ onlineLicense }}</p>
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
/* 在线导入（SnapGene 在线库 / Ensembl） */
.map-online { display: flex; flex-direction: column; gap: 0.5rem; }
.map-online-src { display: flex; gap: 1rem; font-size: 0.9rem; }
.map-online-src label { display: inline-flex; gap: 0.3rem; align-items: center; }
.map-online-set { max-width: 22rem; padding: 0.35rem 0.5rem; }
.map-online-error { color: var(--error-color); font-size: 0.9rem; }
.map-online-list {
  list-style: none; margin: 0; padding: 0; max-height: 16rem; overflow-y: auto;
  display: flex; flex-direction: column; gap: 0.25rem;
}
.map-online-list li { display: flex; gap: 0.6rem; align-items: center; padding: 0.3rem 0.1rem; border-bottom: 1px solid var(--border-color); }
.map-online-id { font-family: Consolas, Menlo, 'Courier New', monospace; font-size: 0.8rem; color: var(--primary-color); }
.map-online-name { flex: 1; font-size: 0.875rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.map-online-license { border-top: 1px solid var(--border-color); padding-top: 0.4rem; }
.map-options { display: flex; gap: 0.6rem 1.2rem; align-items: center; flex-wrap: wrap; font-size: 0.9rem; }
.map-options label { display: inline-flex; gap: 0.4rem; align-items: center; }
.map-select { padding: 0.35rem 0.5rem; border: 1px solid var(--border-color); border-radius: 6px; background: var(--bg-primary); }
.map-error { color: var(--error-color); background: #fef2f2; border-radius: 6px; padding: 0.5rem 0.75rem; font-size: 0.9rem; }
.map-muted { color: var(--text-secondary); font-size: 0.85rem; }
/* 另存为载体（需管理员） */
.map-saveas { display: flex; flex-direction: column; gap: 0.5rem; }
.map-saveas-msg { font-size: 0.85rem; }
.map-empty { color: var(--text-secondary); text-align: center; padding: 2rem; }
@media (max-width: 640px) {
  h1 { font-size: 1.5rem; }
  .map-drop { padding: 1.25rem 0.75rem; }
  .map-head-actions { width: 100%; }
  .map-head-actions .btn { flex: 1; }
}
</style>
