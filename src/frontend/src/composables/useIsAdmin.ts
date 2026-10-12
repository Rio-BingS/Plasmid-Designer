/**
 * 是否管理员——用于隐藏"只有管理员能用"的写入口（导入/上传/编辑/删除）。
 *
 * 为什么不直接在页面里 `useAuthStore()`：图谱页、载体库页在单测与嵌入式宿主里
 * 会被裸 mount（tests/setup.ts 不装 pinia），缺 pinia 时 `useAuthStore()` 直接
 * 抛错。这里在无 pinia 时降级为 false（即只读界面），后端的 403 仍是最外层防线。
 * 后端才是权威：这些入口隐藏只是别让人白点一次。
 */
import { computed, type ComputedRef } from 'vue'
import { getActivePinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'

export function useIsAdmin(): ComputedRef<boolean> {
  if (!getActivePinia()) return computed(() => false)
  const auth = useAuthStore()
  return computed(() => auth.isAdmin)
}

/** 是否已登录（同样在无 pinia 时降级为 false）——SnapGene 在线库只对登录用户开放 */
export function useIsAuthenticated(): ComputedRef<boolean> {
  if (!getActivePinia()) return computed(() => false)
  const auth = useAuthStore()
  return computed(() => auth.isAuthenticated)
}
