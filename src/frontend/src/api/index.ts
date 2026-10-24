/**
 * API 调用层兼容入口：实现按域拆在同目录的各模块（client/design/vectors/
 * batch/online_sources/analysis/cache/auth/admin/sequencing），这里统一
 * re-export，现有 `from '@/api'` 的调用方不需要改。
 */
export * from './client'
export * from './design'
export * from './vectors'
export * from './batch'
export * from './online_sources'
export * from './analysis'
export * from './cache'
export * from './auth'
export * from './admin'
export * from './sequencing'
