// 粘贴的序列常带换行/空格/制表符（FASTA 分行、Windows \r\n），原样提交会让
// 后端把空白计入长度/坐标、跨行位点漏检、GC 被稀释。所有分析请求统一先去空白
export function cleanSequence(raw: string): string {
  return (raw || '').replace(/\s+/g, '')
}
