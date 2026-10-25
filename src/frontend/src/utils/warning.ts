/**
 * 警告文案拆分：后端警告是「简洁摘要：详细解释」的一句话长文本，
 * 展示时摘要做标题行、详细解释折叠起来（用户反馈全句平铺过于冗余）。
 */

export interface SplitWarning {
  /** 冒号前的短摘要（无冒号时为整句） */
  summary: string
  /** 冒号后的详细解释；无冒号时为空 */
  detail: string
}

export function splitWarning(text: string): SplitWarning {
  const s = text ?? ''
  // 全角冒号是中文文案的标准分隔符，直接拆；半角冒号要求后随空格，
  // 避免误拆 URL/时间（如 https://、12:30）
  const full = s.indexOf('：')
  const half = s.search(/:\s/)
  let i = -1
  if (full >= 0 && half >= 0) i = Math.min(full, half)
  else if (full >= 0) i = full
  else i = half
  if (i < 0) return { summary: s, detail: '' }
  return { summary: s.slice(0, i), detail: s.slice(i + 1).trim() }
}
