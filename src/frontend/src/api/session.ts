/**
 * 登录会话代际（epoch）
 *
 * 登录令牌放在后端下发的 httpOnly Cookie 里，前端读不到、也不再保存，
 * 无法再用「请求所带 token === 当前 token」判断一个 401 属于哪个会话。
 * 改为维护一个会话代际计数：每次建立或结束会话（登录/注册/验证/登出/
 * 被动清理）都递增，请求发出时记下当时的代际。迟到的 401 只有在代际
 * 未变时才清理登录态——旧会话在途请求晚返回的 401 不会踢掉新会话。
 *
 * 独立成模块（而非放进 api/index.ts）：auth store 与 api 拦截器都依赖它，
 * 单测里 mock '@/api' 时也不必逐个补这些导出。
 */

let epoch = 0

/** 当前会话代际 */
export function currentAuthEpoch(): number {
  return epoch
}

/** 会话发生切换（登录/登出/清理）时调用，返回新的代际 */
export function bumpAuthEpoch(): number {
  epoch += 1
  return epoch
}
