/** 图谱导出：当前 SVG → .svg / 2 倍 .png；GenBank 走后端 /api/map/export/genbank */
import { downloadBlob } from '@/utils/download'

export function safeFilename(name: string): string {
  return (name || 'plasmid').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 80) || 'plasmid'
}

/** 克隆 SVG 成独立文档：补 xmlns、白底、固定像素尺寸（样式均为内联属性，不依赖页面 CSS） */
export function serializeSvg(svg: SVGSVGElement): { text: string; width: number; height: number } {
  const clone = svg.cloneNode(true) as SVGSVGElement
  const vb = (svg.getAttribute('viewBox') || '0 0 800 800').split(/\s+/).map(Number)
  const width = Number(svg.getAttribute('width')) || vb[2]
  const height = Number(svg.getAttribute('height')) || vb[3]
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
  clone.setAttribute('width', String(width))
  clone.setAttribute('height', String(height))
  clone.removeAttribute('class')
  clone.removeAttribute('style')
  const bg = clone.ownerDocument.createElementNS('http://www.w3.org/2000/svg', 'rect')
  bg.setAttribute('x', String(vb[0]))
  bg.setAttribute('y', String(vb[1]))
  bg.setAttribute('width', String(vb[2]))
  bg.setAttribute('height', String(vb[3]))
  bg.setAttribute('fill', '#ffffff')
  clone.insertBefore(bg, clone.firstChild)
  const text = '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(clone)
  return { text, width, height }
}

export function downloadSvg(svg: SVGSVGElement, name: string): void {
  const { text } = serializeSvg(svg)
  downloadBlob(new Blob([text], { type: 'image/svg+xml' }), `${safeFilename(name)}.svg`)
}

export function downloadPng(svg: SVGSVGElement, name: string, scale = 2): Promise<void> {
  const { text, width, height } = serializeSvg(svg)
  return new Promise((resolve, reject) => {
    const img = new Image()
    const url = URL.createObjectURL(new Blob([text], { type: 'image/svg+xml' }))
    img.onload = () => {
      try {
        const canvas = document.createElement('canvas')
        canvas.width = Math.round(width * scale)
        canvas.height = Math.round(height * scale)
        const ctx = canvas.getContext('2d')
        if (!ctx) throw new Error('浏览器不支持 Canvas')
        ctx.scale(scale, scale)
        ctx.drawImage(img, 0, 0, width, height)
        URL.revokeObjectURL(url)
        canvas.toBlob((blob) => {
          if (!blob) return reject(new Error('PNG 生成失败'))
          downloadBlob(blob, `${safeFilename(name)}.png`)
          resolve()
        }, 'image/png')
      } catch (e) {
        URL.revokeObjectURL(url)
        reject(e)
      }
    }
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('SVG 渲染失败')) }
    img.src = url
  })
}
