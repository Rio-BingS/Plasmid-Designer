<script setup lang="ts">
import SnapgeneSetPicker from '../components/SnapgeneSetPicker.vue'
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import type { VectorInfo } from '@/types'
import { useIsAdmin, useIsAuthenticated } from '@/composables/useIsAdmin'
import {
  getVectors,
  deleteVector,
  getVectorSequence,
  searchNcbi,
  previewNcbi,
  importFromNcbiId,
} from '@/api'
import { downloadTextFile } from '@/utils/download'
import { safeHttpUrl } from '@/utils/safeUrl'
import { useOnlineImport } from '@/composables/useOnlineImport'
import VectorUploadModal from '@/components/VectorUploadModal.vue'
import VectorBatchImportModal from '@/components/VectorBatchImportModal.vue'
import VectorEditModal from '@/components/VectorEditModal.vue'
import MapThumbnail from '@/map/components/MapThumbnail.vue'
import { previewToThumb } from '@/map/preview'

const router = useRouter()
// 载体库是共享库但写入需管理员：非管理员不显示导入/上传入口（点了也只会 403）
const canImport = useIsAdmin()

// 上传/批量导入/编辑三个对话框与在线来源面板的实现分别迁到
// components/Vector*Modal.vue 与 composables/useOnlineImport.ts（纯搬运）
const showUploadModal = ref(false)
const showBatchModal = ref(false)
const showEditModal = ref(false)
const editingVector = ref<VectorInfo | null>(null)

const loggedIn = useIsAuthenticated()
const vectors = ref<VectorInfo[]>([])
// 列表缩略图：来自 GET /api/vectors 的 map_preview，缺省时不画
const thumbs = computed(() => new Map(vectors.value.map((v) => [v.id, previewToThumb(v.map_preview, v.name)])))
const loading = ref(true)
const filterType = ref('')
const filterHost = ref('')

// 编辑对话框

// 序列下载
const downloadingId = ref('')

// NCBI 搜索
const ncbiQuery = ref('')
const ncbiResults = ref<any[]>([])
const ncbiSearching = ref(false)
const previewData = ref<any>(null)
const showPreviewModal = ref(false)
const importingId = ref('')

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

// 编辑载体
function openEditModal(vector: VectorInfo) {
  editingVector.value = vector
  showEditModal.value = true
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

const {
  activeTab,
  onlineMsg, onlinePreview, showOnlinePreview,
  snapgeneAvailable, snapgeneLicense, addgeneLicense,
  sgSets, sgSetId, sgRows, sgLoading, sgError, sgSearch,
  ensemblId, ensemblType, ensemblLoading, ensemblLicense,
  addgeneTerm, addgeneRows, addgeneMeta, addgeneError, addgeneLoading,
  addgeneImporting, addgeneSession, onlineImporting,
  uniprotQuery, uniprotReviewed, uniprotRows, uniprotEntry,
  uniprotNucleotide, uniprotError, uniprotLoading, uniprotLicense,
  ensureOnlineSources, switchTab, loadSnapgeneSets, loadSnapgeneIndex,
  searchSnapgeneByName, openSnapgenePreview, importSnapgeneRow,
  openEnsemblPreview, confirmOnlineImport, doSearchAddgene,
  doAddgeneMetadata, doAddgeneImport, doSearchUniprot, openUniprotEntry,
} = useOnlineImport({ loggedIn, reload: loadVectors })
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

    <VectorUploadModal v-model="showUploadModal" @uploaded="loadVectors" />

    <VectorBatchImportModal v-model="showBatchModal" @imported="loadVectors" />

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

  <VectorEditModal v-model="showEditModal" :vector="editingVector" @saved="loadVectors" />
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
