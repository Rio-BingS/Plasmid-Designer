<script setup lang="ts">
/** 批量导入载体对话框（从 VectorsView.vue 迁出，纯搬运） */
import { ref } from 'vue'
import { batchImportVectors } from '@/api'

const show = defineModel<boolean>({ default: false })
const emit = defineEmits<{ imported: [] }>()

const batchNcbiIds = ref('')
const batchImporting = ref(false)
const batchResult = ref('')

async function handleBatchImport() {
  const ids = batchNcbiIds.value.split('\n').map(s => s.trim()).filter(Boolean)
  if (ids.length === 0) return
  try {
    batchImporting.value = true
    batchResult.value = ''
    const result = await batchImportVectors(ids)
    batchResult.value = `成功导入 ${result.ncbi.length} 个载体` +
      (result.errors.length > 0 ? `，${result.errors.length} 个失败` : '')
    emit('imported')
  } catch (e: any) {
    batchResult.value = `批量导入失败: ${e.response?.data?.detail || e.message}`
  } finally {
    batchImporting.value = false
  }
}
</script>

<template>
  <div v-if="show" class="modal-overlay" @click.self="show = false">
    <div class="modal">
      <div class="modal-header">
        <h2>批量导入载体</h2>
        <button class="btn-close" @click="show = false">✕</button>
      </div>
      <div class="modal-body">
        <p class="hint">输入 NCBI 序列 ID，每行一个</p>
        <textarea v-model="batchNcbiIds" placeholder="例：&#10;JX185437.1&#10;HQ670549.1" rows="5" class="form-textarea"></textarea>
        <div v-if="batchResult" :class="['result-msg', batchResult.includes('成功') ? 'success' : 'error']">
          {{ batchResult }}
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" @click="show = false">取消</button>
        <button class="btn btn-primary" :disabled="!batchNcbiIds.trim() || batchImporting" @click="handleBatchImport">
          {{ batchImporting ? '导入中...' : '批量导入' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; justify-content: center; align-items: center; z-index: 1000; }
.modal { background: white; border-radius: 12px; width: 90%; max-width: 500px; max-height: 85vh; overflow-y: auto; }
.modal-header { display: flex; justify-content: space-between; align-items: center; padding: 1.25rem 1.5rem; border-bottom: 1px solid var(--border-color); }
.modal-header h2 { font-size: 1.125rem; margin: 0; }
.btn-close { background: none; border: none; cursor: pointer; font-size: 1.25rem; color: var(--text-secondary); padding: 0; }
.modal-body { padding: 1.5rem; }
.modal-footer { display: flex; justify-content: flex-end; gap: 0.75rem; padding: 1rem 1.5rem; border-top: 1px solid var(--border-color); }
.hint { font-size: 0.875rem; color: var(--text-secondary); margin-bottom: 1rem; }
.form-textarea { width: 100%; padding: 0.75rem; border: 1px solid var(--border-color); border-radius: 8px; font-family: monospace; font-size: 0.875rem; resize: vertical; }
.result-msg { padding: 0.75rem 1rem; border-radius: 8px; font-size: 0.875rem; margin-top: 0.75rem; }
.result-msg.success { background: #dcfce7; color: #166534; }
.result-msg.error { background: #fee2e2; color: #991b1b; }
</style>
