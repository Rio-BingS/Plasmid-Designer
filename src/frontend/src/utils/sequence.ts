// 粘贴的序列常带换行/空格/制表符（FASTA 分行、Windows \r\n），原样提交会让
// 后端把空白计入长度/坐标、跨行位点漏检、GC 被稀释。所有分析请求统一先去空白。
//
// 带名称的 CDS 也很常见：`>PNPM5` 这样的 FASTA 头里的名称要单独取出来给结果和
// 导出用，头行本身不能当碱基提交。

/** 输入里的 FASTA 头行数（0 表示普通序列） */
function hasHeader(raw: string): boolean {
  return (raw || '').split(/\r?\n/).some((line) => line.trim().startsWith('>'))
}

/** 去空白；带 FASTA 头时丢掉头行，只留碱基 */
export function cleanSequence(raw: string): string {
  const text = raw || ''
  if (!hasHeader(text)) return text.replace(/\s+/g, '')
  return text
    .split(/\r?\n/)
    .filter((line) => !line.trim().startsWith('>'))
    .join('')
    .replace(/\s+/g, '')
}

/**
 * 输入 → { name, sequence }：FASTA 头的第一个名称作为序列名（去掉前导 `>`，
 * 只取标题里第一个空白前的内容），其余行按碱基拼接。
 * 没有 FASTA 头时 name 为空串，sequence 与 cleanSequence 完全一致。
 */
export function parseNamedSequence(raw: string): { name: string; sequence: string } {
  const text = raw || ''
  if (!hasHeader(text)) return { name: '', sequence: cleanSequence(text) }
  let name = ''
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim()
    if (!t.startsWith('>')) continue
    name = t.slice(1).trim().split(/\s+/)[0] || ''
    break
  }
  return { name, sequence: cleanSequence(text) }
}
