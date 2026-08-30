<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import type { VectorInfo } from '@/types'
import {
  getVectors,
  deleteVector,
  updateVector,
  uploadVectorFile,
  batchImportVectors,
  getVectorSequence,
  searchNcbi,
  previewNcbi,
  importFromNcbiId
} from '@/api'

const router = useRouter()
const vectors = ref<VectorInfo[]>([])
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
const activeTab = ref<'local' | 'ncbi'>('local')
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
    const blob = new Blob([content], { type: 'text/plain' })
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.setAttribute('download', `${vectorId}.${ext}`)
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)
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

async function openPreview(seqId) {
  try {
    previewData.value = await previewNcbi(seqId)
    showPreviewModal.value = true
  } catch (e) {
    alert('Preview failed')
  }
}

async function confirmImport(seqId) {
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
})
</script>

<template>
  <div class="vectors-page">
    <div class="page-header">
      <div>
        <h1>载体库</h1>
        <p class="subtitle">浏览、导入和管理可用载体</p>
      </div>
      <div class="header-actions">
        <button class="btn btn-secondary" @click="showUploadModal = true">📁 上传文件</button>
        <button class="btn btn-secondary" @click="showBatchModal = true">📦 批量导入</button>
      </div>
    </div>

    <div class="source-tabs">
      <button class="source-tab" :class="{ active: activeTab === 'local' }" @click="activeTab = 'local'">本地载体</button>
      <button class="source-tab" :class="{ active: activeTab === 'ncbi' }" @click="activeTab = 'ncbi'">NCBI 搜索</button>
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
      <button class="btn btn-primary" @click="showUploadModal = true">导入第一个载体</button>
    </div>
    <div v-else class="vectors-grid">
      <div v-for="vector in vectors" :key="vector.id" class="vector-card" @click="viewVector(vector.id)">
        <div class="card-header">
          <h3>{{ vector.name }}</h3>
          <div class="card-actions" @click.stop>
            <button class="btn-icon" title="下载 FASTA" @click="downloadSequence(vector.id, 'fasta', $event)">🧬</button>
            <button class="btn-icon" title="下载 GenBank" @click="downloadSequence(vector.id, 'genbank', $event)">📄</button>
            <button class="btn-icon" title="编辑" @click="openEditModal(vector)">✏️</button>
            <button class="btn-icon btn-icon-danger" title="删除" @click="handleDelete(vector.id, $event)">🗑️</button>
          </div>
        </div>
        <p class="vector-source">{{ vector.source }}</p>
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
          <a :href="item.url" target="_blank" class="ncbi-link">NCBI 链接 ↗</a>
        </div>
        <div class="ncbi-result-actions">
          <button class="btn btn-small btn-secondary" @click="openPreview(item.id)">👁️ 预览</button>
          <button class="btn btn-small btn-primary" :disabled="importingId === item.id" @click="confirmImport(item.id)">
            {{ importingId === item.id ? '导入中...' : '📥 导入' }}
          </button>
        </div>
      </div>
    </div>
    <div v-else-if="ncbiQuery && !ncbiSearching" class="empty-text">输入关键词后点击搜索</div>
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
        <button class="btn btn-primary" :disabled="importingId" @click="confirmImport(previewData.id)">📥 确认导入</button>
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
  grid-template-columns: repeat(2, 1fr);
  gap: 1.5rem;
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
.source-tab { padding: 0.75rem 1.5rem; background: none; border: none; cursor: pointer; font-size: 0.95rem; color: var(--text-secondary); border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all 0.2s; }
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
