<script setup lang="ts">
/** 上传载体文件对话框（从 VectorsView.vue 迁出，纯搬运） */
import { ref } from 'vue'
import { uploadVectorFile } from '@/api'

const show = defineModel<boolean>({ default: false })
const emit = defineEmits<{ uploaded: [] }>()

const uploadFile = ref<File | null>(null)
const uploading = ref(false)
const uploadResult = ref('')

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
    emit('uploaded')
  } catch (e: any) {
    uploadResult.value = `导入失败: ${e.response?.data?.detail || e.message}`
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <div v-if="show" class="modal-overlay" @click.self="show = false">
    <div class="modal">
      <div class="modal-header">
        <h2>上传载体文件</h2>
        <button class="btn-close" @click="show = false">✕</button>
      </div>
      <div class="modal-body">
        <p class="hint">支持 GenBank (.gb, .gbk) 格式文件</p>
        <input type="file" accept=".gb,.gbk,.dna" @change="handleFileSelect" class="file-input" />
        <div v-if="uploadResult" :class="['result-msg', uploadResult.includes('成功') ? 'success' : 'error']">
          {{ uploadResult }}
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" @click="show = false">取消</button>
        <button class="btn btn-primary" :disabled="!uploadFile || uploading" @click="handleUpload">
          {{ uploading ? '上传中...' : '导入' }}
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
.file-input { width: 100%; padding: 0.5rem; border: 1px dashed var(--border-color); border-radius: 8px; margin-bottom: 1rem; }
.result-msg { padding: 0.75rem 1rem; border-radius: 8px; font-size: 0.875rem; margin-top: 0.75rem; }
.result-msg.success { background: #dcfce7; color: #166534; }
.result-msg.error { background: #fee2e2; color: #991b1b; }
</style>
