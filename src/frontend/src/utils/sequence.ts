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

/** 异体星号：中文/日文输入法下 Shift+8 打出的是全角＊（U+FF0A），还有﹡∗٭⁎＿ */
const STAR_VARIANTS = /[\uFF0A\uFE61\u2217\u066D\u204E\uFF3F]/g

/**
 * 输入字符归一：全角/异体星号 → 半角 '*'，全角字母数字 → 半角。
 *
 * 中文输入法下打出来的终止符是 ＊，后端按单字母代码校验一律报「无效的氨基酸
 * 代码」，用户看到的就是「* 无法识别」。这里在失焦/提交时归一，
 * 不改变换行与空格（那是排版，不是字符问题）。
 */
export function normalizeSequenceInput(raw: string): string {
  if (!raw) return ''
  return raw.normalize('NFKC').replace(STAR_VARIANTS, '*')
}

/** 氨基酸单字母代码 + 终止符（与后端 core.codon_optimizer 的口径一致） */
export const AMINO_ACID_CHARS = 'ACDEFGHIKLMNPQRSTVWY*'

/** IUPAC DNA 碱基（含模糊码）：设计页 5′/3′ 接头后端按 IUPAC 校验，
 * Kozak 共识常写作 GCCRCCATGG（R=A/G），这里保持同一口径 */
export const IUPAC_DNA_CHARS = 'ACGTRYSWKMBDHVN'

/** 找出手动输入里不被支持的字符（返回前若干个，附 1-based 位置） */
export function findUnsupportedChars(
  seq: string,
  allowed: string,
  limit = 3
): { char: string; index: number }[] {
  // 与后端 normalize_sequence_text 的「先归一后校验」同一口径：小写 acgt 是
  // 合法输入（提交时后端统一转大写），这里按大小写不敏感判定，避免被前端拦下
  const allowedSet = new Set(allowed.toUpperCase())
  const out: { char: string; index: number }[] = []
  const text = seq || ''
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]
    // 空白与零宽字符由后端统一剔除（FASTA 分行、粘贴带的空格），不算非法字符
    if (/[\s\u200b-\u200d\u2060\ufeff]/.test(ch)) continue
    if (allowedSet.has(ch.toUpperCase())) continue
    out.push({ char: ch, index: i + 1 })
    if (out.length >= limit) break
  }
  return out
}

/** 不支持字符的提示文案（含位置与合法字母表，用户能直接定位） */
export function describeUnsupportedChars(
  bad: { char: string; index: number }[],
  kind: 'amino_acid' | 'dna'
): string {
  const shown = bad.map((b) => b.char === ' ' ? '空格' : b.char === '\n' ? '换行' : b.char)
    .map((c) => '「' + c + '」')
    .join('、')
  const where = bad.map((b) => '第 ' + b.index + ' 位').join('、')
  const hint = kind === 'amino_acid'
    ? '氨基酸单字母代码：ACDEFGHIKLMNPQRSTVWY，终止符用 *'
    : 'DNA 只接受 A/T/G/C（如需模糊碱基请先在序列分析页处理）'
  return '序列含无法识别的字符 ' + shown + '（' + where + '）；' + hint
}
