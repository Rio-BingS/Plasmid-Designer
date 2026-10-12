/**
 * 外链协议白名单：只放行 http(s) 绝对地址，其余（javascript:、data:、vbscript:、相对路径、
 * 畸形 URL）一律返回 null，由调用方降级为纯文本。Vue 的 :href 不过滤协议，凡是来自
 * 后端/第三方数据（出处 URL、Addgene 元数据、UniProt 交叉引用）的链接都要先过这里。
 */
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const v = value.trim()
  if (!/^https?:\/\//i.test(v)) return null
  try {
    const u = new URL(v)
    return u.protocol === 'http:' || u.protocol === 'https:' ? v : null
  } catch {
    return null
  }
}
