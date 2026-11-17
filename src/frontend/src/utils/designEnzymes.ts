/**
 * 设计用酶（二期 10e）：用户可选填，逗号分隔酶名（如 EcoRI,BamHI）。
 * 留空时后端依次用参考里的 pd_enzyme 标注、范围两端单切酶、全质粒单切酶。
 * 设置存在浏览器本地，单样品与批量共用。
 */
import { ref } from 'vue'

const KEY = 'pd_seq_design_enzymes'

function load(): string {
  try {
    return localStorage.getItem(KEY) ?? ''
  } catch {
    return ''
  }
}

export const designEnzymes = ref<string>(load())

export function saveDesignEnzymes(v: string): void {
  designEnzymes.value = v
  try {
    if (v.trim()) localStorage.setItem(KEY, v.trim())
    else localStorage.removeItem(KEY)
  } catch {
    /* 隐私模式等不可写时只保留本次会话 */
  }
}

/** 规范化：中英文逗号/分号/空格分隔 → 逗号分隔 */
export function normalizeEnzymes(v: string): string {
  return v.split(/[,，;；\s]+/).map((x) => x.trim()).filter(Boolean).join(',')
}

/** 给 FormData 追加 enzymes（非空时） */
export function appendDesignEnzymes(form: FormData): void {
  const v = normalizeEnzymes(designEnzymes.value)
  if (v) form.append('enzymes', v)
}
