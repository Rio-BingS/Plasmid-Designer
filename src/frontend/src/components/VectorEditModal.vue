<script setup lang="ts">
/** 编辑载体信息对话框（从 VectorsView.vue 迁出，纯搬运）。
 * vector 由父组件在打开时传入，watch 填充表单；保存成功后 emit('saved')。 */
import { ref, watch } from 'vue'
import { updateVector } from '@/api'
import type { VectorInfo } from '@/types'

const show = defineModel<boolean>({ default: false })
const props = defineProps<{ vector: VectorInfo | null }>()
const emit = defineEmits<{ saved: [] }>()

const editForm = ref({
  name: '', description: '', vector_type: '', host: '',
  antibiotic_resistance: '', copy_number: ''
})
const editSaving = ref(false)

// immediate：挂载时 vector 已就位（如页面恢复态）也要回填，不只是变更时
watch(() => props.vector, (v) => {
  if (!v) return
  editForm.value = {
    name: v.name,
    description: v.description,
    vector_type: v.vector_type,
    host: v.host.join(', '),
    antibiotic_resistance: v.antibiotic_resistance.join(', '),
    copy_number: v.copy_number
  }
}, { immediate: true })

async function saveEdit() {
  if (!props.vector) return
  try {
    editSaving.value = true
    await updateVector(props.vector.id, {
      name: editForm.value.name,
      description: editForm.value.description,
      vector_type: editForm.value.vector_type,
      host: editForm.value.host.split(',').map(s => s.trim()).filter(Boolean),
      antibiotic_resistance: editForm.value.antibiotic_resistance.split(',').map(s => s.trim()).filter(Boolean),
      copy_number: editForm.value.copy_number
    })
    show.value = false
    emit('saved')
  } catch (e: any) {
    alert(`保存失败: ${e.response?.data?.detail || e.message}`)
  } finally {
    editSaving.value = false
  }
}
</script>

<template>
  <div v-if="show" class="modal-overlay" @click.self="show = false">
    <div class="modal">
      <div class="modal-header">
        <h2>编辑载体信息</h2>
        <button class="btn-close" @click="show = false">✕</button>
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
        <button class="btn btn-secondary" @click="show = false">取消</button>
        <button class="btn btn-primary" :disabled="editSaving" @click="saveEdit">
          {{ editSaving ? '保存中...' : '保存' }}
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
.form-group { margin-bottom: 1rem; }
.form-group label { display: block; font-size: 0.875rem; font-weight: 500; margin-bottom: 0.25rem; color: var(--text-secondary); }
.form-input { width: 100%; padding: 0.5rem 0.75rem; border: 1px solid var(--border-color); border-radius: 8px; }
.form-textarea { width: 100%; padding: 0.75rem; border: 1px solid var(--border-color); border-radius: 8px; font-family: monospace; font-size: 0.875rem; resize: vertical; }
</style>
