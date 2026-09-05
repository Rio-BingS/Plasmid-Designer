<script setup lang="ts">
import { ref } from 'vue'
import { RouterView } from 'vue-router'
import NavBar from './components/NavBar.vue'
import DisclaimerModal from './components/DisclaimerModal.vue'

const disclaimerRef = ref<InstanceType<typeof DisclaimerModal> | null>(null)
</script>

<template>
  <div class="app">
    <NavBar />
    <main class="main-content">
      <RouterView />
    </main>
    <footer class="app-footer">
      <span class="footer-badge">个人项目</span>
      <span>仅供学习与科研参考 · 设计结果与数据未经实验验证，不用于临床/诊断 · 序列数据来源见</span>
      <button class="footer-link" @click="disclaimerRef?.open()">免责声明</button>
    </footer>
    <DisclaimerModal ref="disclaimerRef" />
  </div>
</template>

<style scoped>
.app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.main-content {
  flex: 1;
  padding: 2rem;
  max-width: 1400px;
  margin: 0 auto;
  width: 100%;
}

.app-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 0.45rem;
  padding: 0.8rem 1rem;
  border-top: 1px solid #EEF1F5;
  font-size: 0.75rem;
  color: #9CA3AF;
  background: #FCFDFE;
}

.footer-badge {
  padding: 1px 8px;
  border: 1px solid #DDE3EC;
  border-radius: 999px;
  background: #F4F6FA;
  color: #6B7280;
  font-size: 0.7rem;
}

.footer-link {
  border: none;
  background: none;
  padding: 0;
  font-size: 0.75rem;
  color: #4E79C7;
  cursor: pointer;
}

.footer-link:hover {
  text-decoration: underline;
}
</style>
