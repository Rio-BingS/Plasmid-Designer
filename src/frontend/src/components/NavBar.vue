<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { RouterLink, useRouter } from 'vue-router'
import AuthModal from './AuthModal.vue'

const router = useRouter()
const authStore = useAuthStore()
const showAuthModal = ref(false)
// user from store
const user = ref<any>(null)  // will be set from authStore
const showUserMenu = ref(false)

onMounted(() => {
  authStore.initFromStorage()
  user.value = authStore.user
})

// Watch store changes
import { watch } from 'vue'
watch(() => authStore.user, (newUser) => {
  user.value = newUser
})

function handleAuthClick() {
  if (user.value) {
    showUserMenu.value = !showUserMenu.value
  } else {
    showAuthModal.value = true
  }
}

function handleAuthenticated(userData: any) {
  user.value = userData
  showAuthModal.value = false
}

async function handleLogout() {
  await authStore.logout()
  user.value = null
  showUserMenu.value = false
  router.push('/')
}

function goToBatch() {
  showUserMenu.value = false
  router.push('/batch')
}
</script>

<template>
  <nav class="navbar">
    <div class="nav-brand">
      <span class="logo">🧬</span>
      <RouterLink to="/" class="brand-text">Plasmid Designer</RouterLink>
    </div>
    
    <div class="nav-links">
      <RouterLink to="/" class="nav-link">首页</RouterLink>
      <RouterLink to="/design" class="nav-link">设计</RouterLink>
      <RouterLink to="/batch" class="nav-link">批量设计</RouterLink>
      <RouterLink to="/vectors" class="nav-link">载体库</RouterLink>
      <RouterLink to="/sequencing" class="nav-link">测序分析</RouterLink>
      <RouterLink to="/analysis" class="nav-link">序列工具</RouterLink>
    </div>
    
    <div class="nav-user">
      <div v-if="user" class="user-section">
        <button class="user-btn" @click="handleAuthClick">
          <span class="user-avatar">{{ user.username?.charAt(0).toUpperCase() || 'U' }}</span>
          <span class="user-name">{{ user.username }}</span>
        </button>
        
        <div v-if="showUserMenu" class="user-dropdown">
          <div class="dropdown-header">
            {{ user.email }}
          </div>
          <div class="dropdown-divider"></div>
          <RouterLink to="/batch" class="dropdown-item" @click="goToBatch">
            📦 批量设计
          </RouterLink>
          <button class="dropdown-item logout-btn" @click="handleLogout">
            🚪 退出登录
          </button>
        </div>
      </div>
      
      <button v-else class="login-btn" @click="showAuthModal = true">
        登录 / 注册
      </button>
    </div>
    
    <AuthModal 
      v-if="showAuthModal" 
      @close="showAuthModal = false"
      @authenticated="handleAuthenticated"
    />
  </nav>
</template>

<style scoped>
.navbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 2rem;
  background-color: white;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  position: relative;
}

.nav-brand {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.logo {
  font-size: 1.5rem;
}

.brand-text {
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--primary-color);
  text-decoration: none;
}

.nav-links {
  display: flex;
  gap: 1.5rem;
}

.nav-link {
  color: var(--text-secondary);
  text-decoration: none;
  font-weight: 500;
  transition: color 0.2s;
}

.nav-link:hover,
.nav-link.router-link-active {
  color: var(--primary-color);
}

.nav-user {
  position: relative;
}

.user-section {
  position: relative;
}

.user-btn {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 1rem;
  background: var(--bg-secondary);
  border: none;
  border-radius: 9999px;
  cursor: pointer;
  transition: all 0.2s;
}

.user-btn:hover {
  background: #E5E7EB;
}

.user-avatar {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--primary-color);
  color: white;
  border-radius: 50%;
  font-weight: 600;
  font-size: 0.875rem;
}

.user-name {
  font-weight: 500;
  color: #333;
}

.login-btn {
  padding: 0.5rem 1.25rem;
  background: var(--primary-color);
  color: white;
  border: none;
  border-radius: 9999px;
  cursor: pointer;
  font-weight: 500;
  transition: all 0.2s;
}

.login-btn:hover {
  background: #4338CA;
}

.user-dropdown {
  position: absolute;
  top: calc(100% + 0.5rem);
  right: 0;
  background: white;
  border-radius: 8px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
  min-width: 200px;
  overflow: hidden;
  z-index: 100;
}

.dropdown-header {
  padding: 0.75rem 1rem;
  font-size: 0.75rem;
  color: #666;
  border-bottom: 1px solid #eee;
}

.dropdown-divider {
  height: 1px;
  background: #eee;
}

.dropdown-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  padding: 0.75rem 1rem;
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
  color: #333;
  text-decoration: none;
  transition: background 0.2s;
}

.dropdown-item:hover {
  background: #F3F4F6;
}

.logout-btn {
  color: #DC2626;
}
</style>
