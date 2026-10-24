// 本文件由 api/index.ts 按域拆分而来（纯搬运）：index.ts 保留 re-export 兼容层，
// 新代码请直接从 '@/api/<域>' 导入。

import { api } from './client'
import type {
  CacheClearResult, CacheInfo
} from '@/types'

export async function getCacheInfo(): Promise<CacheInfo> {
  const response = await api.get('/cache/stats')
  return response.data
}

// 清除缓存
export async function clearCache(): Promise<CacheClearResult> {
  const response = await api.post('/cache/clear')
  return response.data
}

// 综合序列分析
export async function invalidateDesignCache(designId: string): Promise<any> {
  const response = await api.post(`/cache/invalidate/design/${encodeURIComponent(designId)}`)
  return response.data
}

// 缓存 — 载体缓存失效
export async function invalidateVectorCache(vectorId: string): Promise<any> {
  const response = await api.post(`/cache/invalidate/vector/${encodeURIComponent(vectorId)}`)
  return response.data
}

// 缓存 — 健康检查
export async function getCacheHealth(): Promise<any> {
  const response = await api.get('/cache/health')
  return response.data
}

// 速率限制 — 状态
export async function getRateLimitStatus(): Promise<any> {
  const response = await api.get('/rate-limit/status')
  return response.data
}

// 速率限制 — 配置
export async function getRateLimitConfig(): Promise<any> {
  const response = await api.get('/rate-limit/config')
  return response.data
}

// ==================== Sanger 测序分析 ====================

/** 逐列对齐视图：read 与参考的原始比对证据（供人工核对）
 *  ref_aligned/read_aligned 等长，'-' 为该列缺失（插入/缺失）；read 以参考方向展示 */
