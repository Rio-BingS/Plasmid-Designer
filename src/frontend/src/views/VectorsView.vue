<script setup lang="ts">
import SnapgeneSetPicker from '../components/SnapgeneSetPicker.vue'
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import type { VectorInfo } from '@/types'
import { useIsAdmin, useIsAuthenticated } from '@/composables/useIsAdmin'
import {
  getVectors,
  deleteVector,
  updateVector,
  uploadVectorFile,
  batchImportVectors,
  getVectorSequence,
  searchNcbi,
  previewNcbi,
  importFromNcbiId,
  getOnlineSources,
  getSnapgeneSets,
  getSnapgeneSetIndex,
  searchSnapgene,
  gatewayErrorText,
  previewSnapgene,
  importFromSnapgene,
  previewEnsembl,
  importFromEnsembl,
  searchAddgeneIndex,
  getAddgeneMetadata,
  importFromAddgene,
  searchUniprot,
  getUniprotEntry
} from '@/api'
import type { OnlinePreview, OnlineSourceInfo, UniProtNucleotide } from '@/api'
import { downloadTextFile } from '@/utils/download'
import { safeHttpUrl } from '@/utils/safeUrl'
import MapThumbnail from '@/map/components/MapThumbnail.vue'
import { previewToThumb } from '@/map/preview'

const router = useRouter()
// 载体库是共享库但写入需管理员：非管理员不显示导入/上传入口（点了也只会 403）
const canImport = useIsAdmin()
const loggedIn = useIsAuthenticated()
const vectors = ref<VectorInfo[]>([])
// 列表缩略图：来自 GET /api/vectors 的 map_preview，缺省时不画
const thumbs = computed(() => new Map(vectors.value.map((v) => [v.id, previewToThumb(v.map_preview, v.name)])))
const loading = ref(true)
const filterType = ref('')
const filterHost = ref('')

// 上传对话框
const showUploadModal = ref(false)
const uploadFile = ref<File | null>(null)
const uploading = ref(false)
const uploadResult = ref('')

// 批量导入对话框
const showBatchModal = ref(false)
const batchNcbiIds = ref('')
const batchImporting = ref(false)
const batchResult = ref('')

// 编辑对话框
const showEditModal = ref(false)
const editingVector = ref<VectorInfo | null>(null)
const editForm = ref({ name: '', description: '', vector_type: '', host: '', antibiotic_resistance: '', copy_number: '' })
const editSaving = ref(false)

// 序列下载
const downloadingId = ref('')

// NCBI 搜索
const activeTab = ref<'local' | 'ncbi' | 'snapgene' | 'ensembl' | 'addgene' | 'uniprot'>('local')
const ncbiQuery = ref('')
const ncbiResults = ref<any[]>([])
const ncbiSearching = ref(false)
const previewData = ref<any>(null)
const showPreviewModal = ref(false)
const importingId = ref('')

// 在线序列来源（SnapGene 在线库 / Ensembl / Addgene）
// 许可提示随 /vectors/import/sources 下发；Addgene 后端明确不绕登录，前端只做引导
const onlineSources = ref<OnlineSourceInfo[]>([])
const onlineNotice = ref('')
const onlineMsg = ref('')
const onlinePreview = ref<OnlinePreview | null>(null)
const showOnlinePreview = ref(false)
// SnapGene 在线库
const sgSets = ref<{ set_id: string; name: string }[]>([])
const sgSetId = ref('')
const sgRows = ref<any[]>([])
const sgLoading = ref(false)
const sgError = ref('')
/** 非空 = 正在显示跨集合搜索结果（集合框里输入的是质粒名） */
const sgSearch = ref('')
/** 预览弹窗对应的集合（搜索结果来自不同集合，不能用当前集合） */
const sgPreviewSetId = ref('')
// SnapGene 在线库默认开启（SNAPGENE_ONLINE_ENABLED=false 可关）且只对登录用户开放：
// 两者都满足才显示页签（后端仍是权威：关 → 404，匿名 → 401）
const snapgeneEnabled = ref(false)
const snapgeneAvailable = computed(() => snapgeneEnabled.value && loggedIn.value)
// Ensembl
const ensemblId = ref('')
const ensemblType = ref('cds')
const ensemblLoading = ref(false)
// Addgene
const addgeneTerm = ref('')
const addgeneRows = ref<any[]>([])
const addgeneMeta = ref<any>(null)
const addgeneError = ref('')
const addgeneLoading = ref(false)
// Addgene 全长序列导入（需管理员 + 后端已配置用户自备会话）
const addgeneImporting = ref(false)
/** SnapGene / Ensembl 在线导入在途锁：防双击重复导入（或第二次得到 409） */
const onlineImporting = ref(false)
// UniProt（蛋白库反查：条目 → 核酸交叉引用 → 去 NCBI/Ensembl 取 DNA）
const uniprotQuery = ref('')
const uniprotReviewed = ref(false)
const uniprotRows = ref<any[]>([])
const uniprotEntry = ref<any>(null)
const uniprotNucleotide = ref<UniProtNucleotide[]>([])
const uniprotError = ref('')
const uniprotLoading = ref(false)

const snapgeneLicense = computed(
  () => onlineSources.value.find(s => s.id === 'snapgene')?.license || '')
const ensemblLicense = computed(
  () => onlineSources.value.find(s => s.id === 'ensembl')?.license || '')
const addgeneLicense = computed(
  () => onlineSources.value.find(s => s.id === 'addgene')?.license || '')
const uniprotLicense = computed(
  () => onlineSources.value.find(s => s.id === 'uniprot')?.license || '')
// 部署方是否配了 Addgene 会话（决定「导入为本地载体」按钮是否可用）
const addgeneSession = computed(
  () => onlineSources.value.find(s => s.id === 'addgene')?.session_configured === true)

async function loadVectors() {
  try {
    loading.value = true
    const data = await getVectors(filterType.value, filterHost.value)
    vectors.value = data
  } catch (e) {
    console.error('Failed to load vectors:', e)
  } finally {
    loading.value = false
  }
}

function viewVector(vectorId: string) {
  router.push({ name: 'vector-detail', params: { id: vectorId } })
}

// 上传载体文件
function handleFileSelect(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files && input.files.length > 0) {
    uploadFile.value = input.files[0]
  }
}

async function handleUpload() {
  if (!uploadFile.value) return
  try {
    uploading.value = true
    uploadResult.value = ''
    const result = await uploadVectorFile(uploadFile.value)
    uploadResult.value = `导入成功: ${result.vector.name} (${result.vector.length} bp, ${result.vector.features} 个特征)`
    await loadVectors()
  } catch (e: any) {
    uploadResult.value = `导入失败: ${e.response?.data?.detail || e.message}`
  } finally {
    uploading.value = false
  }
}

// 批量导入
async function handleBatchImport() {
  const ids = batchNcbiIds.value.split('\n').map(s => s.trim()).filter(Boolean)
  if (ids.length === 0) return
  try {
    batchImporting.value = true
    batchResult.value = ''
    const result = await batchImportVectors(ids)
    batchResult.value = `成功导入 ${result.imported_ncbi} 个载体` +
      (result.errors.length > 0 ? `，${result.errors.length} 个失败` : '')
    await loadVectors()
  } catch (e: any) {
    batchResult.value = `批量导入失败: ${e.response?.data?.detail || e.message}`
  } finally {
    batchImporting.value = false
  }
}

// 编辑载体
function openEditModal(vector: VectorInfo) {
  editingVector.value = vector
  editForm.value = {
    name: vector.name,
    description: vector.description,
    vector_type: vector.vector_type,
    host: vector.host.join(', '),
    antibiotic_resistance: vector.antibiotic_resistance.join(', '),
    copy_number: vector.copy_number
  }
  showEditModal.value = true
}

async function saveEdit() {
  if (!editingVector.value) return
  try {
    editSaving.value = true
    await updateVector(editingVector.value.id, {
      name: editForm.value.name,
      description: editForm.value.description,
      vector_type: editForm.value.vector_type,
      host: editForm.value.host.split(',').map(s => s.trim()).filter(Boolean),
      antibiotic_resistance: editForm.value.antibiotic_resistance.split(',').map(s => s.trim()).filter(Boolean),
      copy_number: editForm.value.copy_number
    })
    showEditModal.value = false
    await loadVectors()
  } catch (e: any) {
    alert(`保存失败: ${e.response?.data?.detail || e.message}`)
  } finally {
    editSaving.value = false
  }
}

// 删除载体
async function handleDelete(vectorId: string, event: Event) {
  event.stopPropagation()
  if (!confirm('确定要删除此载体吗？')) return
  try {
    await deleteVector(vectorId)
    await loadVectors()
  } catch (e: any) {
    alert(`删除失败: ${e.response?.data?.detail || e.message}`)
  }
}

// 下载序列
async function downloadSequence(vectorId: string, format: string, event: Event) {
  event.stopPropagation()
  try {
    downloadingId.value = vectorId
    const content = await getVectorSequence(vectorId, format)
    const ext = format === 'genbank' ? 'gb' : 'fasta'
    downloadTextFile(content, `${vectorId}.${ext}`)
  } catch (e: any) {
    alert(`下载失败: ${e.response?.data?.detail || e.message}`)
  } finally {
    downloadingId.value = ''
  }
}


// NCBI 搜索
async function handleSearchNcbi() {
  if (!ncbiQuery.value.trim()) return
  try {
    ncbiSearching.value = true
    const result = await searchNcbi(ncbiQuery.value)
    ncbiResults.value = result.results || []
  } catch (e) {
    alert('NCBI search failed')
  } finally {
    ncbiSearching.value = false
  }
}

async function openPreview(seqId: string) {
  try {
    previewData.value = await previewNcbi(seqId)
    showPreviewModal.value = true
  } catch (e) {
    alert('Preview failed')
  }
}

async function confirmImport(seqId: string) {
  try {
    importingId.value = seqId
    await importFromNcbiId(seqId)
    showPreviewModal.value = false
    previewData.value = null
    await loadVectors()
    alert('Import success!')
  } catch (e) {
    alert('Import failed')
  } finally {
    importingId.value = ''
  }
}

onMounted(() => {
  loadVectors()
  ensureOnlineSources()
})

// ==================== 在线来源（SnapGene 在线库 / Ensembl / Addgene） ====================

function errText(e: any, fallback: string): string {
  return gatewayErrorText(e) || e?.response?.data?.detail || e?.message || fallback
}

async function ensureOnlineSources() {
  if (onlineSources.value.length) return
  try {
    const res = await getOnlineSources()
    onlineSources.value = res.sources || []
    onlineNotice.value = res.disclaimer || ''
    snapgeneEnabled.value = res.snapgene_online_enabled === true
  } catch {
    // 未登录/未开放功能时静默：各面板自己的错误位会显示具体原因
  }
}

function switchTab(tab: 'local' | 'ncbi' | 'snapgene' | 'ensembl' | 'addgene' | 'uniprot') {
  if (tab === 'snapgene' && !snapgeneAvailable.value) return
  activeTab.value = tab
  onlineMsg.value = ''
  ensureOnlineSources()
  if (tab === 'snapgene' && sgSets.value.length === 0 && !sgLoading.value) loadSnapgeneSets()
}

async function loadSnapgeneSets() {
  sgLoading.value = true
  sgError.value = ''
  try {
    const res = await getSnapgeneSets()
    sgSets.value = res.sets || []
    if (!sgSetId.value && sgSets.value.length) sgSetId.value = sgSets.value[0].set_id
    if (sgSetId.value) await loadSnapgeneIndex()
  } catch (e: any) {
    sgError.value = errText(e, '在线库集合加载失败')
  } finally {
    sgLoading.value = false
  }
}

// 集合索引请求号：快速切换集合时只认最后一次请求，乱序返回的旧集合不覆盖列表
let sgIndexGen = 0
async function loadSnapgeneIndex() {
  if (!sgSetId.value) return
  sgSearch.value = ''
  const gen = ++sgIndexGen
  sgLoading.value = true
  sgError.value = ''
  sgRows.value = []
  try {
    const res = await getSnapgeneSetIndex(sgSetId.value)
    if (gen === sgIndexGen) sgRows.value = res.plasmids || []
  } catch (e: any) {
    if (gen === sgIndexGen) sgError.value = errText(e, '集合索引加载失败')
  } finally {
    if (gen === sgIndexGen) sgLoading.value = false
  }
}

/** 集合框输入的不是集合：按质粒名跨集合搜索（如 pET-30a） */
async function searchSnapgeneByName(q: string) {
  const gen = ++sgIndexGen
  sgSearch.value = q
  sgLoading.value = true
  sgError.value = ''
  sgRows.value = []
  try {
    const res = await searchSnapgene(q)
    if (gen === sgIndexGen) sgRows.value = res.plasmids || []
  } catch (e: any) {
    if (gen === sgIndexGen) sgError.value = errText(e, '搜索失败')
  } finally {
    if (gen === sgIndexGen) sgLoading.value = false
  }
}

function rowSetId(row: any): string {
  return row?.set_id || sgSetId.value
}

async function openSnapgenePreview(row: any) {
  onlineMsg.value = ''
  try {
    sgPreviewSetId.value = rowSetId(row)
    onlinePreview.value = await previewSnapgene(sgPreviewSetId.value, row.plasmid_id)
    showOnlinePreview.value = true
  } catch (e: any) {
    onlineMsg.value = `预览失败: ${errText(e, '')}`
  }
}

/** 列表里直接导入（省去预览一步） */
async function importSnapgeneRow(row: any) {
  if (onlineImporting.value) return
  onlineImporting.value = true
  onlineMsg.value = ''
  try {
    const res = await importFromSnapgene(rowSetId(row), row.plasmid_id)
    onlineMsg.value = `已导入 ${res.vector.name}（${res.vector.length} bp）`
    await loadVectors()
  } catch (e: any) {
    onlineMsg.value = `导入失败: ${errText(e, '')}`
  } finally {
    onlineImporting.value = false
  }
}

async function openEnsemblPreview() {
  if (!ensemblId.value.trim()) return
  ensemblLoading.value = true
  onlineMsg.value = ''
  try {
    onlinePreview.value = await previewEnsembl(ensemblId.value.trim(), ensemblType.value)
    showOnlinePreview.value = true
  } catch (e: any) {
    onlineMsg.value = `预览失败: ${errText(e, '')}`
  } finally {
    ensemblLoading.value = false
  }
}

/** 预览弹窗里的"确认导入"：只对可导入来源生效（Addgene 不在此列） */
async function confirmOnlineImport() {
  const p = onlinePreview.value
  if (!p || onlineImporting.value) return
  onlineImporting.value = true
  onlineMsg.value = ''
  try {
    if (p.source.startsWith('ensembl')) {
      const res = await importFromEnsembl(p.id, ensemblType.value)
      onlineMsg.value = `已导入 ${res.vector.name}（${res.vector.length} bp）`
    } else {
      const res = await importFromSnapgene(sgPreviewSetId.value || sgSetId.value, p.id)
      onlineMsg.value = `已导入 ${res.vector.name}（${res.vector.length} bp）`
    }
    showOnlinePreview.value = false
    await loadVectors()
  } catch (e: any) {
    onlineMsg.value = `导入失败: ${errText(e, '')}`
  } finally {
    onlineImporting.value = false
  }
}

async function doSearchAddgene() {
  if (!addgeneTerm.value.trim()) return
  addgeneLoading.value = true
  addgeneError.value = ''
  addgeneRows.value = []
  addgeneMeta.value = null
  try {
    const res = await searchAddgeneIndex(addgeneTerm.value.trim())
    addgeneRows.value = res.results || []
    if (addgeneRows.value.length === 0) addgeneError.value = res.note || '本地索引无命中'
  } catch (e: any) {
    addgeneError.value = errText(e, 'Addgene 搜索失败')
  } finally {
    addgeneLoading.value = false
  }
}

async function doAddgeneMetadata(id: string) {
  addgeneLoading.value = true
  addgeneError.value = ''
  addgeneMeta.value = null
  try {
    addgeneMeta.value = await getAddgeneMetadata(id, addgeneSession.value)
  } catch (e: any) {
    addgeneError.value = errText(e, 'Addgene 元数据查询失败')
  } finally {
    addgeneLoading.value = false
  }
}

/** 用部署方配置的（用户自备）Addgene 会话把全长序列导入载体库 */
async function doAddgeneImport(id: string) {
  const pid = String(id || '').replace('#', '').trim()
  if (!pid || addgeneImporting.value) return
  addgeneImporting.value = true
  onlineMsg.value = ''
  addgeneError.value = ''
  try {
    const res = await importFromAddgene(pid)
    onlineMsg.value = `已导入 ${res.vector.name}（${res.vector.length} bp，${res.vector.features} 个特征）`
    await loadVectors()
  } catch (e: any) {
    addgeneError.value = `导入失败: ${errText(e, '')}`
  } finally {
    addgeneImporting.value = false
  }
}

// -------------------- UniProt（蛋白库：只反查，不给质粒序列） --------------------

async function doSearchUniprot() {
  const q = uniprotQuery.value.trim()
  if (!q) return
  uniprotLoading.value = true
  uniprotError.value = ''
  uniprotRows.value = []
  uniprotEntry.value = null
  uniprotNucleotide.value = []
  try {
    const res = await searchUniprot(q, 10, uniprotReviewed.value)
    uniprotRows.value = res.results || []
    if (uniprotRows.value.length === 0) uniprotError.value = 'UniProt 无命中'
  } catch (e: any) {
    uniprotError.value = errText(e, 'UniProt 检索失败')
  } finally {
    uniprotLoading.value = false
  }
}

async function openUniprotEntry(accession: string) {
  if (!accession) return
  uniprotLoading.value = true
  uniprotError.value = ''
  uniprotEntry.value = null
  uniprotNucleotide.value = []
  try {
    const res = await getUniprotEntry(accession)
    uniprotEntry.value = res.entry
    uniprotNucleotide.value = res.nucleotide || []
  } catch (e: any) {
    uniprotError.value = errText(e, 'UniProt 条目查询失败')
  } finally {
    uniprotLoading.value = false
  }
}
</script>

<template>
  <div class="vectors-page">
    <div class="page-header">
      <div>
        <h1>载体库</h1>
        <p class="subtitle">浏览、导入和管理可用载体</p>
      </div>
      <div class="header-actions">
        <template v-if="canImport">
          <button class="btn btn-secondary" @click="showUploadModal = true">📁 上传文件</button>
          <button class="btn btn-secondary" @click="showBatchModal = true">📦 批量导入</button>
        </template>
        <span v-else class="header-hint" title="载体库是共享库，导入/上传需要管理员权限">
          共享载体库 · 导入需管理员
        </span>
      </div>
    </div>

    <div class="source-tabs">
      <button class="source-tab" :class="{ active: activeTab === 'local' }" @click="switchTab('local')">本地载体</button>
      <button class="source-tab" :class="{ active: activeTab === 'ncbi' }" @click="switchTab('ncbi')">NCBI 搜索</button>
      <button v-if="snapgeneAvailable" class="source-tab" :class="{ active: activeTab === 'snapgene' }" @click="switchTab('snapgene')">SnapGene 在线库</button>
      <button class="source-tab" :class="{ active: activeTab === 'ensembl' }" @click="switchTab('ensembl')">Ensembl</button>
      <button class="source-tab" :class="{ active: activeTab === 'addgene' }" @click="switchTab('addgene')">Addgene</button>
      <button class="source-tab" :class="{ active: activeTab === 'uniprot' }" @click="switchTab('uniprot')">UniProt</button>
    </div>

    <div v-if="activeTab === 'local'">
    <div class="filters">
      <select v-model="filterType" @change="loadVectors" class="form-select">
        <option value="">所有类型</option>
        <option value="expression">表达载体</option>
        <option value="cloning">克隆载体</option>
      </select>
      <select v-model="filterHost" @change="loadVectors" class="form-select">
        <option value="">所有宿主</option>
        <option value="E.coli">E.coli</option>
        <option value="mammalian">哺乳动物细胞</option>
      </select>
    </div>

    <div v-if="loading" class="loading">
      <div class="spinner"></div>
    </div>
    <div v-else-if="vectors.length === 0" class="empty-state">
      <p>暂无载体数据</p>
      <button v-if="canImport" class="btn btn-primary" @click="showUploadModal = true">导入第一个载体</button>
    </div>
    <div v-else class="vectors-grid">
      <div v-for="vector in vectors" :key="vector.id" class="vector-card" @click="viewVector(vector.id)">
        <div class="card-header">
          <h3>{{ vector.name }}</h3>
          <div class="card-actions" @click.stop>
            <button class="btn-icon" title="下载 FASTA" @click="downloadSequence(vector.id, 'fasta', $event)">🧬</button>
            <button class="btn-icon" title="下载 GenBank" @click="downloadSequence(vector.id, 'genbank', $event)">📄</button>
            <button v-if="canImport" class="btn-icon" title="编辑（需管理员）" @click="openEditModal(vector)">✏️</button>
            <button v-if="canImport" class="btn-icon btn-icon-danger" title="删除（需管理员）" @click="handleDelete(vector.id, $event)">🗑️</button>
          </div>
        </div>
        <p class="vector-source">{{ vector.source }}</p>
        <div v-if="thumbs.get(vector.id)" class="vector-thumb">
          <MapThumbnail :doc="thumbs.get(vector.id)!" :size="72" />
          <span class="vector-thumb-meta">{{ thumbs.get(vector.id)!.length.toLocaleString() }} bp · {{ thumbs.get(vector.id)!.topology === 'circular' ? '环状' : '线性' }}</span>
        </div>
        <div class="vector-info">
          <div class="info-item">
            <span class="label">类型:</span>
            <span>{{ vector.vector_type }}</span>
          </div>
          <div class="info-item">
            <span class="label">宿主:</span>
            <span>{{ vector.host.join(', ') }}</span>
          </div>
          <div class="info-item">
            <span class="label">抗性:</span>
            <span>{{ vector.antibiotic_resistance.join(', ') }}</span>
          </div>
          <div class="info-item">
            <span class="label">MCS酶:</span>
            <span>{{ vector.mcs_enzymes.slice(0, 5).join(', ') }}</span>
          </div>
        </div>
        <p class="vector-description">{{ vector.description }}</p>
      </div>
    </div>

    <!-- 上传文件对话框 -->
    <div v-if="showUploadModal" class="modal-overlay" @click.self="showUploadModal = false">
      <div class="modal">
        <div class="modal-header">
          <h2>上传载体文件</h2>
          <button class="btn-close" @click="showUploadModal = false">✕</button>
        </div>
        <div class="modal-body">
          <p class="hint">支持 GenBank (.gb, .gbk) 格式文件</p>
          <input type="file" accept=".gb,.gbk,.dna" @change="handleFileSelect" class="file-input" />
          <div v-if="uploadResult" :class="['result-msg', uploadResult.includes('成功') ? 'success' : 'error']">
            {{ uploadResult }}
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" @click="showUploadModal = false">取消</button>
          <button class="btn btn-primary" :disabled="!uploadFile || uploading" @click="handleUpload">
            {{ uploading ? '上传中...' : '导入' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 批量导入对话框 -->
    <div v-if="showBatchModal" class="modal-overlay" @click.self="showBatchModal = false">
      <div class="modal">
        <div class="modal-header">
          <h2>批量导入载体</h2>
          <button class="btn-close" @click="showBatchModal = false">✕</button>
        </div>
        <div class="modal-body">
          <p class="hint">输入 NCBI 序列 ID，每行一个</p>
          <textarea v-model="batchNcbiIds" placeholder="例：&#10;JX185437.1&#10;HQ670549.1" rows="5" class="form-textarea"></textarea>
          <div v-if="batchResult" :class="['result-msg', batchResult.includes('成功') ? 'success' : 'error']">
            {{ batchResult }}
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" @click="showBatchModal = false">取消</button>
          <button class="btn btn-primary" :disabled="!batchNcbiIds.trim() || batchImporting" @click="handleBatchImport">
            {{ batchImporting ? '导入中...' : '批量导入' }}
          </button>
        </div>
      </div>
    </div>

    </div><!-- end local tab -->

  <!-- NCBI 搜索 Tab -->
  <div v-if="activeTab === 'ncbi'" class="ncbi-search-section">
    <div class="ncbi-search-box">
      <input v-model="ncbiQuery" class="form-input" placeholder="搜索 NCBI 载体 (关键词或ID)..." @keyup.enter="handleSearchNcbi" />
      <button class="btn btn-primary" :disabled="ncbiSearching || !ncbiQuery.trim()" @click="handleSearchNcbi">
        {{ ncbiSearching ? '搜索中...' : '🔍 搜索' }}
      </button>
    </div>

    <div v-if="ncbiResults.length > 0" class="ncbi-results">
      <h3>搜索结果 ({{ ncbiResults.length }})</h3>
      <div v-for="item in ncbiResults" :key="item.id" class="ncbi-result-item">
        <div class="ncbi-result-info">
          <span class="ncbi-id">{{ item.id }}</span>
          <a v-if="safeHttpUrl(item.url)" :href="safeHttpUrl(item.url)!" target="_blank" rel="noopener" class="ncbi-link">NCBI 链接 ↗</a>
        </div>
        <div class="ncbi-result-actions">
          <button class="btn btn-small btn-secondary" @click="openPreview(item.id)">👁️ 预览</button>
          <button v-if="canImport" class="btn btn-small btn-primary" :disabled="importingId === item.id" @click="confirmImport(item.id)">
            {{ importingId === item.id ? '导入中...' : '📥 导入' }}
          </button>
        </div>
      </div>
    </div>
    <div v-else-if="ncbiQuery && !ncbiSearching" class="empty-text">输入关键词后点击搜索</div>
  </div>

  <!-- SnapGene 在线库 Tab -->
  <div v-if="activeTab === 'snapgene' && snapgeneAvailable" class="online-section">
    <p class="online-license">{{ snapgeneLicense }}</p>
    <div class="online-row">
      <SnapgeneSetPicker v-model="sgSetId" :sets="sgSets" input-class="form-input"
                         @change="loadSnapgeneIndex" @search="searchSnapgeneByName" />
      <button class="btn btn-secondary" :disabled="sgLoading" @click="loadSnapgeneSets">
        {{ sgLoading ? '加载中...' : '🔄 刷新' }}
      </button>
    </div>
    <div v-if="sgError" class="result-msg error">{{ sgError }}</div>
    <div v-else-if="sgLoading" class="loading"><div class="spinner"></div>
      <span v-if="sgSearch" class="empty-text">正在全部集合中搜索「{{ sgSearch }}」，首次约 20 秒…</span></div>
    <div v-else-if="sgRows.length === 0" class="empty-text">
      {{ sgSearch ? `没有找到「${sgSearch}」，换个写法试试（如 pET-30a、pET28）` : '该集合没有条目' }}</div>
    <div v-else class="online-list">
      <div v-for="row in sgRows" :key="(row.set_id || '') + '/' + row.plasmid_id" class="online-list-item">
        <div class="online-item-info">
          <span class="online-id">{{ row.plasmid_id }}</span>
          <span class="online-name">{{ row.name }}</span>
          <span class="online-file">{{ row.set_name ? `集合：${row.set_name}` : row.filename }}</span>
        </div>
        <div class="online-item-actions">
          <button class="btn btn-small btn-secondary" @click="openSnapgenePreview(row)">👁️ 预览</button>
          <button v-if="canImport" class="btn btn-small btn-primary" :disabled="onlineImporting" @click="importSnapgeneRow(row)">📥 导入</button>
        </div>
      </div>
    </div>
    <div v-if="onlineMsg" :class="['result-msg', onlineMsg.includes('失败') ? 'error' : 'success']">
      {{ onlineMsg }}
    </div>
  </div>

  <!-- Ensembl Tab -->
  <div v-if="activeTab === 'ensembl'" class="online-section">
    <p class="online-license">{{ ensemblLicense }}</p>
    <div class="online-row">
      <input v-model="ensemblId" class="form-input" placeholder="Ensembl ID，如 ENSG00000139618 / ENST00000288602" @keyup.enter="openEnsemblPreview" />
      <select v-model="ensemblType" class="form-select">
        <option value="cds">CDS（编码序列）</option>
        <option value="cdna">cDNA</option>
        <option value="genomic">基因组</option>
      </select>
      <button class="btn btn-primary" :disabled="ensemblLoading || !ensemblId.trim()" @click="openEnsemblPreview">
        {{ ensemblLoading ? '获取中...' : '🔍 预览' }}
      </button>
    </div>
    <p class="online-hint">导入的是线性片段（非质粒）；蛋白序列不能作为图谱序列导入。</p>
  </div>

  <!-- Addgene Tab（只做索引检索与元数据，不绕登录） -->
  <div v-if="activeTab === 'addgene'" class="online-section">
    <p class="online-license">{{ addgeneLicense }}</p>
    <div class="online-row">
      <input v-model="addgeneTerm" class="form-input" placeholder="搜索质粒名或质粒号（本地索引，不联网）" @keyup.enter="doSearchAddgene" />
      <button class="btn btn-primary" :disabled="addgeneLoading || !addgeneTerm.trim()" @click="doSearchAddgene">
        {{ addgeneLoading ? '搜索中...' : '🔍 搜索' }}
      </button>
    </div>
    <div v-if="addgeneError" class="result-msg error">{{ addgeneError }}</div>
    <div v-if="addgeneRows.length > 0" class="online-list">
      <div v-for="row in addgeneRows" :key="row.id" class="online-list-item">
        <div class="online-item-info">
          <span class="online-id">#{{ row.id }}</span>
          <span class="online-name">{{ row.name }}</span>
        </div>
        <div class="online-item-actions">
          <a class="ncbi-link" :href="`https://www.addgene.org/${encodeURIComponent(String(row.id))}/`" target="_blank" rel="noopener">页面 ↗</a>
          <button class="btn btn-small btn-secondary" @click="doAddgeneMetadata(String(row.id))">元数据</button>
        </div>
      </div>
    </div>
    <div v-if="addgeneMeta" class="addgene-meta">
      <p><strong>#{{ addgeneMeta.plasmid_id }}</strong> — 序列文件需登录，本工具不代为登录。</p>
      <p class="online-hint">{{ addgeneMeta.note }}</p>
      <p class="online-hint">可用直链：
        <a v-if="safeHttpUrl(addgeneMeta.metadata?.mapPngUrl)" :href="safeHttpUrl(addgeneMeta.metadata?.mapPngUrl)!" target="_blank" rel="noopener">map.png ↗</a>
        <a v-if="safeHttpUrl(addgeneMeta.metadata?.genbankUrl)" :href="safeHttpUrl(addgeneMeta.metadata?.genbankUrl)!" target="_blank" rel="noopener">.gbk（需登录）↗</a>
      </p>
    </div>
    <div class="online-row">
      <button v-if="canImport && addgeneSession" class="btn btn-primary"
              :disabled="addgeneImporting || !(addgeneMeta || addgeneTerm.trim())"
              @click="doAddgeneImport(addgeneMeta?.plasmid_id || addgeneTerm)">
        {{ addgeneImporting ? '导入中...' : '📥 用我的登录态导入为本地载体' }}
      </button>
      <span v-else-if="canImport" class="online-hint">
        未配置 ADDGENE_SESSION_COOKIE：本工具不代登录、不绕登录墙——
        在浏览器登录 Addgene 后把会话 Cookie 配到该环境变量即可直接导入。
      </span>
    </div>
    <div v-if="onlineMsg" :class="['result-msg', onlineMsg.includes('失败') ? 'error' : 'success']">
      {{ onlineMsg }}
    </div>
    <p class="online-hint">也可直接输入质粒号查元数据：
      <button class="btn btn-small btn-secondary" @click="doAddgeneMetadata(addgeneTerm.replace('#','').trim())">查 #{{ addgeneTerm.replace('#','').trim() || '…' }}</button>
    </p>
  </div>

  <!-- UniProt Tab（蛋白库：按基因/蛋白名反查核酸来源，不直接给质粒序列） -->
  <div v-if="activeTab === 'uniprot'" class="online-section">
    <p class="online-license">{{ uniprotLicense }}</p>
    <div class="online-row">
      <input v-model="uniprotQuery" class="form-input" placeholder="基因/蛋白名或登录号，如 TP53、p53 human、P04637" @keyup.enter="doSearchUniprot" />
      <label class="online-hint"><input v-model="uniprotReviewed" type="checkbox" /> 只看已审阅</label>
      <button class="btn btn-primary" :disabled="uniprotLoading || !uniprotQuery.trim()" @click="doSearchUniprot">
        {{ uniprotLoading ? '检索中...' : '🔍 检索' }}
      </button>
    </div>
    <div v-if="uniprotError" class="result-msg error">{{ uniprotError }}</div>
    <div v-if="uniprotRows.length > 0" class="online-list">
      <div v-for="row in uniprotRows" :key="row.accession" class="online-list-item">
        <div class="online-item-info">
          <span class="online-id">{{ row.accession }}</span>
          <span class="online-name">{{ row.name || row.entry_id }}{{ row.gene ? ` · ${row.gene}` : '' }}</span>
          <span class="online-file">{{ row.organism }} · {{ row.length }} aa{{ row.reviewed ? ' · 已审阅' : '' }}</span>
        </div>
        <div class="online-item-actions">
          <button class="btn btn-small btn-secondary" @click="openUniprotEntry(row.accession)">核酸来源</button>
        </div>
      </div>
    </div>
    <div v-if="uniprotEntry" class="addgene-meta">
      <p><strong>{{ uniprotEntry.accession }}</strong> — {{ uniprotEntry.name }}（{{ uniprotEntry.organism }}）
        <a :href="`https://www.uniprot.org/uniprotkb/${encodeURIComponent(uniprotEntry.accession)}/entry`" target="_blank" rel="noopener" class="ncbi-link">UniProt ↗</a>
      </p>
      <p class="online-hint">UniProt 不含质粒序列；下面是该蛋白对应的核酸记录，可去对应站点取 DNA 后再导入：</p>
      <ul v-if="uniprotNucleotide.length" class="online-list">
        <li v-for="c in uniprotNucleotide.slice(0, 12)" :key="c.label" class="online-list-item">
          <div class="online-item-info">
            <span class="online-id">{{ c.source }}</span>
            <span class="online-name">{{ c.label }}</span>
          </div>
          <div class="online-item-actions">
            <a v-if="safeHttpUrl(c.url)" class="ncbi-link" :href="safeHttpUrl(c.url)!" target="_blank" rel="noopener">打开 ↗</a>
            <button class="btn btn-small btn-secondary" @click="ncbiQuery = c.accession; switchTab('ncbi'); handleSearchNcbi()">
              去 NCBI 标签页
            </button>
          </div>
        </li>
      </ul>
      <p v-else class="online-hint">该条目没有可用的核酸交叉引用（可换已审阅条目或直接按基因名检索）。</p>
    </div>
  </div>

  <!-- NCBI 预览弹窗 -->
  <div v-if="showPreviewModal" class="modal-overlay" @click.self="showPreviewModal = false">
    <div class="modal">
      <div class="modal-header">
        <h2>载体预览</h2>
        <button class="btn-close" @click="showPreviewModal = false">✕</button>
      </div>
      <div class="modal-body" v-if="previewData">
        <div class="preview-info">
          <h3>{{ previewData.name }}</h3>
          <div class="info-cards">
            <div class="info-card"><span class="info-value">{{ previewData.length?.toLocaleString() }}</span><span class="info-label">长度 (bp)</span></div>
            <div class="info-card"><span class="info-value">{{ previewData.gc_content?.toFixed(1) }}%</span><span class="info-label">GC 含量</span></div>
            <div class="info-card"><span class="info-value">{{ previewData.features_count }}</span><span class="info-label">特征数</span></div>
          </div>
          <p class="preview-desc">{{ previewData.description }}</p>
          <div v-if="previewData.warnings?.length" class="preview-warnings">
            <p v-for="w in previewData.warnings" :key="w" class="warning-item">⚠️ {{ w }}</p>
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" @click="showPreviewModal = false">取消</button>
        <button v-if="canImport" class="btn btn-primary" :disabled="!!importingId" @click="confirmImport(String(previewData.id))">📥 确认导入</button>
        <span v-else class="online-hint">导入需管理员权限</span>
      </div>
    </div>
  </div>

  <!-- 在线来源预览弹窗（SnapGene 在线库 / Ensembl；Addgene 不进这里） -->
  <div v-if="showOnlinePreview" class="modal-overlay" @click.self="showOnlinePreview = false">
    <div class="modal">
      <div class="modal-header">
        <h2>在线序列预览</h2>
        <button class="btn-close" @click="showOnlinePreview = false">✕</button>
      </div>
      <div class="modal-body" v-if="onlinePreview">
        <div class="preview-info">
          <h3>{{ onlinePreview.name }}</h3>
          <div class="info-cards">
            <div class="info-card"><span class="info-value">{{ onlinePreview.length.toLocaleString() }}</span><span class="info-label">长度 (bp)</span></div>
            <div class="info-card"><span class="info-value">{{ onlinePreview.gc_content.toFixed(1) }}%</span><span class="info-label">GC 含量</span></div>
            <div class="info-card"><span class="info-value">{{ onlinePreview.features.length }}</span><span class="info-label">特征数</span></div>
          </div>
          <p class="preview-desc">
            来源：{{ onlinePreview.source }}（{{ onlinePreview.topology === 'circular' ? '环状' : '线性' }}）
          </p>
          <p class="online-hint">{{ onlinePreview.license }}</p>
          <p v-if="safeHttpUrl(onlinePreview.url)" class="online-hint">
            <a :href="safeHttpUrl(onlinePreview.url)!" target="_blank" rel="noopener">原始来源 ↗</a>
          </p>
          <div v-if="onlinePreview.features.length" class="feature-preview">
            <span v-for="(f, i) in onlinePreview.features.slice(0, 12)" :key="`${f.name || f.type}-${i}`" class="feature-chip">
              {{ f.name || f.type }} <em>{{ f.start }}–{{ f.end }}</em>
            </span>
          </div>
          <div v-if="onlinePreview.warnings?.length" class="preview-warnings">
            <p v-for="w in onlinePreview.warnings" :key="w" class="warning-item">⚠️ {{ w }}</p>
          </div>
          <div v-if="onlineMsg" :class="['result-msg', onlineMsg.includes('失败') ? 'error' : 'success']">
            {{ onlineMsg }}
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" @click="showOnlinePreview = false">取消</button>
        <button v-if="canImport" class="btn btn-primary" :disabled="onlineImporting" @click="confirmOnlineImport">
          {{ onlineImporting ? '导入中...' : '📥 导入到载体库' }}
        </button>
        <span v-else class="online-hint">导入需管理员权限</span>
      </div>
    </div>
  </div>

  <!-- 编辑载体对话框 -->
    <div v-if="showEditModal" class="modal-overlay" @click.self="showEditModal = false">
      <div class="modal">
        <div class="modal-header">
          <h2>编辑载体信息</h2>
          <button class="btn-close" @click="showEditModal = false">✕</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label>名称</label>
            <input v-model="editForm.name" class="form-input" />
          </div>
          <div class="form-group">
            <label>描述</label>
            <textarea v-model="editForm.description" class="form-textarea" rows="3"></textarea>
          </div>
          <div class="form-group">
            <label>类型</label>
            <input v-model="editForm.vector_type" class="form-input" />
          </div>
          <div class="form-group">
            <label>宿主（逗号分隔）</label>
            <input v-model="editForm.host" class="form-input" />
          </div>
          <div class="form-group">
            <label>抗性（逗号分隔）</label>
            <input v-model="editForm.antibiotic_resistance" class="form-input" />
          </div>
          <div class="form-group">
            <label>拷贝数</label>
            <input v-model="editForm.copy_number" class="form-input" />
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" @click="showEditModal = false">取消</button>
          <button class="btn btn-primary" :disabled="editSaving" @click="saveEdit">
            {{ editSaving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.vectors-page {
  max-width: 1200px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 2rem;
}
h1 {
  font-size: 2rem;
  margin-bottom: 0.25rem;
}
.subtitle {
  color: var(--text-secondary);
  margin-bottom: 0;
}
.header-actions {
  display: flex;
  gap: 0.75rem;
}
.filters {
  display: flex;
  gap: 1rem;
  margin-bottom: 2rem;
}
.filters .form-select {
  width: auto;
  min-width: 200px;
}
.vectors-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1.5rem;
}
@media (max-width: 640px) {
  .vectors-grid { grid-template-columns: minmax(0, 1fr); gap: 1rem; }
}
.vector-card {
  background: white;
  padding: 1.5rem;
  border-radius: 0.75rem;
  box-shadow: var(--shadow);
  cursor: pointer;
  transition: box-shadow 0.2s;
}
.vector-card:hover {
  box-shadow: 0 4px 12px rgba(0,0,0,0.12);
}
.vector-thumb {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin: 0.5rem 0;
}
.vector-thumb :deep(.map-thumb) {
  flex: none;
  max-width: 100%;
}
.vector-thumb-meta {
  font-size: 0.8rem;
  color: var(--text-secondary);
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}
.card-header h3 {
  font-size: 1.25rem;
  margin-bottom: 0.25rem;
  color: var(--primary-color);
}
.card-actions {
  display: flex;
  gap: 0.25rem;
  opacity: 0;
  transition: opacity 0.2s;
}
.vector-card:hover .card-actions {
  opacity: 1;
}
.btn-icon {
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px 6px;
  border-radius: 4px;
  font-size: 1rem;
  transition: background 0.2s;
}
.btn-icon:hover {
  background: var(--bg-secondary);
}
.btn-icon-danger:hover {
  background: #fee2e2;
}
.vector-source {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-bottom: 1rem;
}
.vector-info {
  margin-bottom: 1rem;
}
.info-item {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.25rem;
  font-size: 0.875rem;
}
.info-item .label {
  color: var(--text-secondary);
}
.vector-description {
  font-size: 0.875rem;
  color: var(--text-secondary);
  line-height: 1.5;
}
.loading {
  display: flex;
  justify-content: center;
  padding: 3rem;
}
.empty-state {
  text-align: center;
  padding: 4rem 2rem;
  color: var(--text-secondary);
}

/* Modal styles */
.modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 1000;
}
.modal {
  background: white;
  border-radius: 12px;
  width: 90%;
  max-width: 500px;
  max-height: 85vh;
  overflow-y: auto;
}
.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1.25rem 1.5rem;
  border-bottom: 1px solid var(--border-color);
}
.modal-header h2 {
  font-size: 1.125rem;
  margin: 0;
}
.btn-close {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1.25rem;
  color: var(--text-secondary);
  padding: 0;
}
.modal-body {
  padding: 1.5rem;
}
.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
  padding: 1rem 1.5rem;
  border-top: 1px solid var(--border-color);
}
.hint {
  font-size: 0.875rem;
  color: var(--text-secondary);
  margin-bottom: 1rem;
}
.file-input {
  width: 100%;
  padding: 0.5rem;
  border: 1px dashed var(--border-color);
  border-radius: 8px;
  margin-bottom: 1rem;
}
.form-textarea {
  width: 100%;
  padding: 0.75rem;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  font-family: monospace;
  font-size: 0.875rem;
  resize: vertical;
}
.form-group {
  margin-bottom: 1rem;
}
.form-group label {
  display: block;
  font-size: 0.875rem;
  font-weight: 500;
  margin-bottom: 0.25rem;
  color: var(--text-secondary);
}
.form-input {
  width: 100%;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--border-color);
  border-radius: 8px;
}
.result-msg {
  padding: 0.75rem 1rem;
  border-radius: 8px;
  font-size: 0.875rem;
  margin-top: 0.75rem;
}
.result-msg.success {
  background: #dcfce7;
  color: #166534;
}
.result-msg.error {
  background: #fee2e2;
  color: #991b1b;
}

.source-tabs { display: flex; gap: 0.5rem; margin-bottom: 1.5rem; border-bottom: 2px solid var(--border-color); padding-bottom: 0; }
.header-hint { color: var(--text-secondary); font-size: 0.85rem; align-self: center; }.source-tab { padding: 0.75rem 1.5rem; background: none; border: none; cursor: pointer; font-size: 0.95rem; color: var(--text-secondary); border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all 0.2s; }
.source-tab:hover { color: var(--primary-color); }
.source-tab.active { color: var(--primary-color); border-bottom-color: var(--primary-color); font-weight: 600; }
.ncbi-search-section { background: white; padding: 1.5rem; border-radius: 0.75rem; box-shadow: var(--shadow); }
.ncbi-search-box { display: flex; gap: 0.75rem; margin-bottom: 1.5rem; }
.ncbi-search-box .form-input { flex: 1; }
.ncbi-search-box .btn { white-space: nowrap; }
.ncbi-results h3 { font-size: 1rem; margin-bottom: 1rem; }
.ncbi-result-item { display: flex; justify-content: space-between; align-items: center; padding: 0.75rem; border: 1px solid var(--border-color); border-radius: 0.5rem; margin-bottom: 0.5rem; }
.ncbi-result-info { display: flex; align-items: center; gap: 0.75rem; }
.ncbi-id { font-family: monospace; font-size: 0.875rem; color: var(--primary-color); }
.ncbi-link { font-size: 0.75rem; color: var(--text-secondary); text-decoration: none; }
.ncbi-link:hover { color: var(--primary-color); }
.ncbi-result-actions { display: flex; gap: 0.5rem; }

/* 在线序列来源（SnapGene 在线库 / Ensembl / Addgene） */
.online-section { background: white; padding: 1.5rem; border-radius: 0.75rem; box-shadow: var(--shadow); }
.online-license {
  font-size: 0.8rem;
  color: var(--text-secondary);
  background: var(--bg-secondary, #f7f7f8);
  border-left: 3px solid var(--primary-color);
  padding: 0.5rem 0.75rem;
  border-radius: 0.25rem;
  margin-bottom: 1rem;
  line-height: 1.5;
}
.online-row { display: flex; gap: 0.75rem; margin-bottom: 1rem; flex-wrap: wrap; }
.online-row .form-input { flex: 1; min-width: 220px; }
.online-row .form-select { min-width: 200px; }
.online-hint { font-size: 0.8rem; color: var(--text-secondary); margin: 0.35rem 0; line-height: 1.5; }
.online-list { display: flex; flex-direction: column; gap: 0.5rem; max-height: 26rem; overflow-y: auto; }
.online-list-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.6rem 0.75rem; border: 1px solid var(--border-color); border-radius: 0.5rem;
}
.online-item-info { display: flex; align-items: baseline; gap: 0.6rem; min-width: 0; }
.online-id { font-family: monospace; font-size: 0.85rem; color: var(--primary-color); }
.online-name { font-weight: 500; }
.online-file { font-size: 0.75rem; color: var(--text-secondary); }
.online-item-actions { display: flex; align-items: center; gap: 0.5rem; white-space: nowrap; }
.addgene-meta {
  margin-top: 1rem; padding: 0.75rem; border-radius: 0.5rem;
  background: var(--bg-secondary, #f7f7f8); font-size: 0.85rem; line-height: 1.6;
}
.addgene-meta a { margin-left: 0.4rem; }
.feature-preview { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.75rem 0; }
.feature-chip {
  font-size: 0.75rem; padding: 0.2rem 0.5rem; border-radius: 999px;
  background: var(--bg-secondary, #f0f0f2); border: 1px solid var(--border-color);
}
.feature-chip em { color: var(--text-secondary); font-style: normal; margin-left: 0.25rem; }
.btn-small { padding: 0.25rem 0.75rem; font-size: 0.75rem; }
.preview-info h3 { font-size: 1.25rem; margin-bottom: 1rem; }
.preview-info .info-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.75rem; margin-bottom: 1rem; }
.preview-info .info-card { text-align: center; padding: 0.75rem; background: var(--bg-secondary); border-radius: 8px; }
.preview-info .info-value { display: block; font-size: 1.25rem; font-weight: 700; color: var(--primary-color); }
.preview-info .info-label { display: block; font-size: 0.7rem; color: var(--text-secondary); margin-top: 0.25rem; }
.preview-desc { font-size: 0.875rem; color: var(--text-secondary); margin-bottom: 0.75rem; line-height: 1.5; }
.preview-warnings { margin-top: 0.75rem; }
.warning-item { padding: 0.5rem 0.75rem; background: #fef3c7; border-radius: 6px; font-size: 0.8rem; margin-bottom: 0.25rem; }
.empty-text { color: var(--text-secondary); font-size: 0.875rem; text-align: center; padding: 2rem; }

</style>
