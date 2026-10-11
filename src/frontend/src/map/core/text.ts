/**
 * 文本宽度估算（无 DOM，可单测，布局结果确定）。按字符类别给出 em 宽度，
 * CJK 等全角字符按 1em（OVE 曾因中文标签被挤压修过同类问题）。数值略偏大，
 * 宁可多留空隙也不重叠。
 */
export function textWidth(text: string, fontSize: number): number {
  let em = 0
  for (const ch of text) {
    const c = ch.codePointAt(0) ?? 0
    if (c >= 0x2e80) em += 1.0
    else if (/[ilIj.,:;'|!()[\]{} ]/.test(ch)) em += 0.32
    else if (/[mwMW@%]/.test(ch)) em += 0.86
    else if (/[A-Z0-9#&]/.test(ch)) em += 0.66
    else em += 0.56
  }
  return em * fontSize
}

/** 截断到给定宽度（末尾 …），放不下一个字符返回 '' */
export function truncateText(text: string, maxW: number, fontSize: number): string {
  if (textWidth(text, fontSize) <= maxW) return text
  let out = ''
  for (const ch of text) {
    if (textWidth(out + ch + '…', fontSize) > maxW) break
    out += ch
  }
  return out ? out + '…' : ''
}
