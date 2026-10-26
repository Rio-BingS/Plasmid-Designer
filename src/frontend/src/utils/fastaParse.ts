/**
 * 批量序列输入解析：FASTA（>name 头行）与「空行 / --- 分隔的裸序列」混合支持。
 *
 * 批量设计页的粘贴框历史上只认空行/---分隔，序列名只能靠 sequence_names
 * 事后补——FASTA 是基因序列最通用的交付格式（公司合成报告、NCBI 下载都是），
 * 直接在同一粘贴框里识别 > 头行，另支持 .fasta/.fa/.txt 文件上传。
 */

export interface ParsedSequence {
  /** 显示与订购名（FASTA 头行第一个词）；裸序列缺省 sequence_N，可编辑 */
  name: string
  sequence: string
  /** 是否来自 FASTA 头行（预览表里区分「推断名」与「自带名」） */
  named: boolean
}

/** 去除序列行里的空白与数字（FASTA 有时带位置数字），保留字母 */
function cleanSeqLine(line: string): string {
  return line.replace(/[\s0-9]/g, '')
}

/**
 * 解析批量序列输入。
 * 规则：以 > 开头的行开启一条 FASTA 记录（取头行第一个词为名）；
 * 否则按空行或 --- 分隔的裸序列块处理，块内多行拼接。
 * 纯分隔符/空白输入返回空数组。
 */
export function parseBatchInput(text: string): ParsedSequence[] {
  const out: ParsedSequence[] = []
  let current: { name: string | null; lines: string[] } | null = null

  const flush = () => {
    if (!current) return
    const sequence = cleanSeqLine(current.lines.join(''))
    if (sequence) {
      out.push({
        name: current.name || `sequence_${out.length + 1}`,
        sequence,
        named: current.name !== null,
      })
    }
    current = null
  }

  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (line.startsWith('>')) {
      flush()
      // 头行第一个词为名（跳过 >），如 ">A01-1 CDS homo sapiens" → A01-1
      const header = line.slice(1).trim()
      current = { name: header ? header.split(/\s+/)[0] : null, lines: [] }
    } else if (!line || line === '---' || line === '—') {
      flush()
    } else {
      if (!current) current = { name: null, lines: [] }
      current.lines.push(line)
    }
  }
  flush()
  return out
}

/** 按序列类型做字符集合法性检查（与后端 normalize 后的口径一致） */
export function validateParsed(seq: string, type: 'amino_acid' | 'dna'): boolean {
  if (!seq) return false
  if (type === 'dna') return /^[ACGTacgt]+$/.test(seq)
  // 氨基酸：放宽到常见 26 字母 + 终止符 *（后端会归一全角＊并逐字符校验）
  return /^[A-Za-z*]+$/.test(seq)
}
