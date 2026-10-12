export interface SetLike { set_id: string; name: string }

// 与后端 _SET_ID_RE 一致
const SET_ID_RE = /^[A-Za-z0-9_()-]{1,64}$/

/**
 * 把用户输入解析为集合 ID：先按 ID、再按名称（不区分大小写）、再兼容「名称（id）」；再按唯一关键字命中。
 * 都不中时：集合列表已加载 → 返回空串（调用方把它当质粒名去跨集合搜索，例如 pET-30a）；
 * 列表没拿到 → 形似 ID 的原样返回交给后端校验，否则返回空串。
 */
export function resolveSnapgeneSet(raw: string, sets: SetLike[]): string {
  const t = (raw || '').trim()
  if (!t) return ''
  const low = t.toLowerCase()
  const byId = sets.find((s) => s.set_id.toLowerCase() === low)
  if (byId) return byId.set_id
  const byName = sets.find((s) => s.name.trim().toLowerCase() === low)
  if (byName) return byName.set_id
  const m = t.match(/[（(]\s*([^()（）]+?)\s*[)）]\s*$/)
  if (m) return resolveSnapgeneSet(m[1], sets)
  const slug = t.replace(/\s+/g, '_')
  const bySlug = sets.find((s) => s.set_id.toLowerCase() === slug.toLowerCase())
  if (bySlug) return bySlug.set_id
  // 关键字只命中一个集合（名称或 ID 包含）时直接选它，如输入 yeast
  const hits = sets.filter((s) => s.name.toLowerCase().includes(low) || s.set_id.toLowerCase().includes(slug.toLowerCase()))
  if (hits.length === 1) return hits[0].set_id
  if (sets.length) return ''
  return SET_ID_RE.test(slug) ? slug : ''
}

/** 跨集合搜索的最短输入（与后端一致：至少 2 个字母或数字） */
export function snapgeneSearchable(raw: string): boolean {
  return (raw || '').replace(/[^0-9a-z]+/gi, '').length >= 2
}

export function snapgeneSetLabel(id: string, sets: SetLike[]): string {
  if (!id) return ''
  return sets.find((s) => s.set_id === id)?.name ?? id
}
