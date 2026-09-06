import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'
import DesignView from '@/views/DesignView.vue'
import ResultView from '@/views/ResultView.vue'
import VectorsView from '@/views/VectorsView.vue'
import VectorDetailView from '@/views/VectorDetailView.vue'
import BatchDesignView from '@/views/BatchDesignView.vue'
import AnalysisView from '@/views/AnalysisView.vue'
import SequencingView from '@/views/SequencingView.vue'
import BatchSequencingView from '@/views/BatchSequencingView.vue'
import CacheView from '@/views/CacheView.vue'
import AdminView from '@/views/AdminView.vue'
import ForbiddenView from '@/views/ForbiddenView.vue'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/design', name: 'design', component: DesignView, meta: { feature: 'design' } },
    { path: '/batch', name: 'batch', component: BatchDesignView, meta: { feature: 'batch' } },
    { path: '/result/:designId', name: 'result', component: ResultView, props: true, meta: { feature: 'design' } },
    { path: '/vectors', name: 'vectors', component: VectorsView, meta: { feature: 'vectors' } },
    { path: '/vectors/:id', name: 'vector-detail', component: VectorDetailView, props: true, meta: { feature: 'vectors' } },
    { path: '/analysis', name: 'analysis', component: AnalysisView, meta: { feature: 'analysis' } },
    { path: '/sequencing', name: 'sequencing', component: SequencingView, meta: { feature: 'sequencing' } },
    { path: '/sequencing/batch', name: 'sequencing-batch', component: BatchSequencingView, meta: { feature: 'sequencing' } },
    { path: '/cache', name: 'cache', component: CacheView },
    { path: '/admin', name: 'admin', component: AdminView, meta: { adminOnly: true } },
    { path: '/forbidden', name: 'forbidden', component: ForbiddenView }
  ]
})

// 站点功能门控：导航入口已按 site-config 隐藏，这里拦截直链访问。
// 站点配置未加载完成（null）时放行，避免刷新时误拦。
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.siteConfig) {
    await auth.refreshSiteConfig()
  }
  if (to.meta.adminOnly && !auth.isAdmin) {
    return { name: 'home' }
  }
  const feature = to.meta.feature as string | undefined
  if (feature && !auth.featureAllowed(feature)) {
    return { name: 'forbidden', query: { feature } }
  }
  return true
})

export default router
