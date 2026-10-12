/**
 * tablist 键盘导航（WAI-ARIA Tabs）：焦点在某个 tab 上时，←/→ 切到前/后一个（循环），
 * Home/End 切到第一个/最后一个。直接绑在 role="tablist" 容器的 keydown 上；
 * 通过 click() 切换，复用各 tab 已有的点击逻辑。跳过 disabled 与隐藏的 tab。
 */
export function onTabKeydown(e: KeyboardEvent): void {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return
  const list = e.currentTarget as HTMLElement | null
  if (!list) return
  const tabs = Array.from(list.querySelectorAll<HTMLElement>('[role="tab"]'))
    .filter((t) => !(t as HTMLButtonElement).disabled && t.style.display !== 'none')
  const cur = tabs.indexOf(e.target as HTMLElement)
  if (cur < 0 || !tabs.length) return
  const n = tabs.length
  const next = e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : (cur + (e.key === 'ArrowRight' ? 1 : -1) + n) % n
  e.preventDefault()
  tabs[next].focus()
  tabs[next].click()
}
