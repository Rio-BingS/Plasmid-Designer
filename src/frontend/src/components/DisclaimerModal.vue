<script setup lang="ts">
/**
 * 个人项目免责声明：首次访问弹出确认（localStorage 记忆，只弹一次），
 * 页脚入口可随时重新打开完整声明。
 */
import { ref, onMounted } from 'vue'

const STORAGE_KEY = 'disclaimer_ack_v1'
const visible = ref(false)

onMounted(() => {
  try {
    if (!localStorage.getItem(STORAGE_KEY)) visible.value = true
  } catch {
    visible.value = true // 隐身模式等 localStorage 不可用时，每次进入都提示
  }
})

function acknowledge() {
  try {
    localStorage.setItem(STORAGE_KEY, new Date().toISOString())
  } catch { /* 忽略存储失败 */ }
  visible.value = false
}

defineExpose({ open: () => { visible.value = true } })
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="disclaimer-overlay" role="dialog" aria-modal="true" aria-label="使用须知">
        <div class="disclaimer-card">
          <div class="disclaimer-icon">🧬</div>
          <h2 class="disclaimer-title">使用须知与免责声明</h2>
          <div class="disclaimer-body">
            <p>
              本站为<strong>个人学习项目</strong>，非专业生物信息学产品，不含任何形式的技术支持与服务承诺。
            </p>
            <ul>
              <li>设计结果、序列与注释<strong>仅供学习与科研参考</strong>，不构成实验操作建议，<strong>严禁用于临床、诊断或生产用途</strong>；</li>
              <li>内置载体数据来自 <a href="https://www.snapgene.com/plasmids" target="_blank" rel="noopener">SnapGene</a>、
                <a href="https://www.ncbi.nlm.nih.gov/" target="_blank" rel="noopener">NCBI</a> 等公开来源并记录出处，
                但<strong>未经独立实验验证</strong>，使用前请自行核对；</li>
              <li>任何实际实验（含克隆、测序、合成）请由具备资质的专业人员<strong>复核确认</strong>后进行，使用本站结果产生的一切后果由使用者自行承担。</li>
            </ul>
          </div>
          <button class="disclaimer-btn" @click="acknowledge">我已阅读并知晓</button>
        </div>
      </div>
  </Teleport>
</template>

<style scoped>
.disclaimer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 1rem;
}

.disclaimer-card {
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.25);
  max-width: 520px;
  width: 100%;
  padding: 2rem 2.25rem 1.75rem;
  text-align: center;
  max-height: 90vh;
  overflow-y: auto;
}

.disclaimer-icon {
  font-size: 2rem;
  margin-bottom: 0.5rem;
}

.disclaimer-title {
  font-size: 1.2rem;
  margin: 0 0 1rem;
  color: #1F2937;
}

.disclaimer-body {
  text-align: left;
  font-size: 0.875rem;
  line-height: 1.7;
  color: #4B5563;
  background: #F8FAFC;
  border: 1px solid #EEF1F5;
  border-radius: 10px;
  padding: 1rem 1.1rem;
}

.disclaimer-body p {
  margin: 0 0 0.6rem;
}

.disclaimer-body ul {
  margin: 0;
  padding-left: 1.1rem;
}

.disclaimer-body li {
  margin-bottom: 0.45rem;
}

.disclaimer-body li:last-child {
  margin-bottom: 0;
}

.disclaimer-body a {
  color: #4E79C7;
  text-decoration: none;
}

.disclaimer-body a:hover {
  text-decoration: underline;
}

.disclaimer-btn {
  margin-top: 1.25rem;
  padding: 0.6rem 2.2rem;
  border: none;
  border-radius: 8px;
  background: #4E79C7;
  color: #fff;
  font-size: 0.95rem;
  cursor: pointer;
  transition: background 0.15s;
}

.disclaimer-btn:hover {
  background: #3E63A8;
}

.disclaimer-fade-enter-active {
  transition: opacity 0.2s ease;
}

.disclaimer-fade-enter-from {
  opacity: 0;
}
</style>
