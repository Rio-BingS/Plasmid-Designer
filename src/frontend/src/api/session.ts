/**
 * 登录会话代际（epoch）
 *
 * 登录令牌放在后端下发的 httpOnly Cookie 里，前端读不到、也不再保存，
 * 无法再用「请求所带 token === 当前 token」判断一个 401 属于哪个会话。
 * 改为维护一个会话代际计数：每次建立或结束会话（登录/注册/验证/登出/
 * 被动清理）都递增，请求发出时记下当时的代际。迟到的 401 只有在代际
 * 未变时才清理登录态——旧会话在途请求晚返回的 401 不会踢掉新会话。
 *
 * 代际跨标签页同步：Cookie 是所有标签页共享的，而 JS 变量各自独立。
 * A 标签页登出后重新登录时，B 若停在旧代际，B 中早先发出、此刻返回的
 * 401 会被判成「当代」，把 B 的界面清成未登录——而 Cookie 其实是有效的
 * 新会话。这里用 BroadcastChannel + localStorage 同步代际，收到别处的
 * 新代际就跟随，并通过 onSessionChanged 通知 auth store 重新核实。
 *
 * 独立成模块（而非放进 api/index.ts）：auth store 与 api 拦截器都依赖它，
 * 单测里 mock '@/api' 时也不必逐个补这些导出。
 */

let epoch = 0

const EPOCH_STORAGE_KEY = 'pd_auth_epoch'
const EPOCH_CHANNEL = 'pd_auth_epoch'

/** 当前会话代际 */
export function currentAuthEpoch(): number {
  return epoch
}

/** 会话发生切换（登录/登出/清理）时调用，返回新的代际 */
export function bumpAuthEpoch(): number {
  epoch += 1
  publishEpoch(epoch)
  return epoch
}

// ---------------- 跨标签页同步 ----------------

let bc: BroadcastChannel | null = null
let externalHandler: (() => void) | null = null

/** 其他标签页发生会话切换时回调（用于重新核实本页登录态） */
export function onSessionChanged(handler: (() => void) | null): void {
  externalHandler = handler
}

function publishEpoch(next: number): void {
  try {
    bc?.postMessage({ type: 'auth-epoch', epoch: next })
  } catch {
    // 通道不可用（隐私模式/宿主没有 BroadcastChannel）：退回 storage 事件
  }
  try {
    localStorage.setItem(EPOCH_STORAGE_KEY, String(next))
  } catch {
    // localStorage 不可用时没有跨标签页同步，单标签页行为不受影响
  }
}

/** 只接受严格更新的代际，避免两页互相「跟随」形成回声 */
function adoptRemoteEpoch(next: number): void {
  if (!Number.isFinite(next) || next <= epoch) return
  epoch = next
  externalHandler?.()
}

if (typeof window !== 'undefined') {
  try {
    bc = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel(EPOCH_CHANNEL) : null
    if (bc) {
      bc.onmessage = (e: MessageEvent) => {
        if (e.data?.type === 'auth-epoch') adoptRemoteEpoch(Number(e.data.epoch))
      }
    }
  } catch {
    bc = null
  }
  // storage 事件只在「其他标签页」触发，正好作为 BroadcastChannel 的兜底
  window.addEventListener('storage', (e) => {
    if (e.key !== EPOCH_STORAGE_KEY || e.newValue === null) return
    adoptRemoteEpoch(Number(e.newValue))
  })
  // 启动时对齐已有代际（例如本页是后打开的）
  try {
    const stored = Number(localStorage.getItem(EPOCH_STORAGE_KEY))
    if (Number.isFinite(stored) && stored > epoch) epoch = stored
  } catch {
    // ignore
  }
}

// 会话失效回调：由 auth store 在创建时注册。api 拦截器只调用这里，
// 不直接引用 auth store——auth store 静态依赖 api，反向再引用就成环，
// 原先的动态 import 又会让构建报「既被动态又被静态导入」
let expiredHandler: (() => void) | null = null

/** 注册会话失效（401）时的清理回调；重复注册以最后一次为准 */
export function onSessionExpired(handler: (() => void) | null): void {
  expiredHandler = handler
}

/** 通知会话已失效；尚无 auth store（如单测环境）时无状态可清 */
export function notifySessionExpired(): void {
  expiredHandler?.()
}
