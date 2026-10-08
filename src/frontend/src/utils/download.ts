/**
 * 浏览器端文件下载工具 — blob 下载的唯一实现
 *
 * 此前 VectorsView / VectorDetailView / SequencingPanel 各有一份内联的
 * createObjectURL → click → revoke 流程（revoke 时机与错误处理还各不相同）。
 * 统一收敛到这里：
 * - 纯文本内容（string）→ 构造 Blob 后下载；
 * - 已是 Blob 的响应体 → 直接下载；
 * - 下载失败统一抛 Error（由调用方决定提示方式）。
 */

/** 下载文本内容（UTF-8） */
export function downloadTextFile(content: string, filename: string): void {
  downloadBlob(new Blob([content], { type: 'text/plain' }), filename)
}

/** 下载 Blob 数据（完成 click 后立即释放 object URL） */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', filename)
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
