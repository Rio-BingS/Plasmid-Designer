import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'
import DesignView from '@/views/DesignView.vue'
import ResultView from '@/views/ResultView.vue'
import VectorsView from '@/views/VectorsView.vue'
import VectorDetailView from '@/views/VectorDetailView.vue'
import BatchDesignView from '@/views/BatchDesignView.vue'
import AnalysisView from '@/views/AnalysisView.vue'
import SequencingView from '@/views/SequencingView.vue'
import CacheView from '@/views/CacheView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/design', name: 'design', component: DesignView },
    { path: '/batch', name: 'batch', component: BatchDesignView },
    { path: '/result/:designId', name: 'result', component: ResultView, props: true },
    { path: '/vectors', name: 'vectors', component: VectorsView },
    { path: '/vectors/:id', name: 'vector-detail', component: VectorDetailView, props: true },
    { path: '/analysis', name: 'analysis', component: AnalysisView },
    { path: '/sequencing', name: 'sequencing', component: SequencingView },
    { path: '/cache', name: 'cache', component: CacheView }
  ]
})

export default router
