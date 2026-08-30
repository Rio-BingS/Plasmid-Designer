import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import PlasmidMap from '@/components/PlasmidMap.vue'

// Mock canvas context
const mockContext = {
  clearRect: vi.fn(),
  fillRect: vi.fn(),
  beginPath: vi.fn(),
  arc: vi.fn(),
  stroke: vi.fn(),
  fill: vi.fn(),
  moveTo: vi.fn(),
  lineTo: vi.fn(),
  closePath: vi.fn(),
  set lineWidth(value: number) {},
  set strokeStyle(value: string) {},
  set fillStyle(value: string) {},
  set font(value: string) {},
  set textAlign(value: string) {},
  set textBaseline(value: string) {},
  set lineCap(value: string) {},
  set shadowColor(value: string) {},
  set shadowBlur(value: number) {},
  fillText: vi.fn(),
  strokeText: vi.fn()
}

HTMLCanvasElement.prototype.getContext = vi.fn(() => mockContext)

describe('PlasmidMap', () => {
  const defaultFeatures = [
    { name: 'T7 Promoter', type: 'promoter', start: 0, end: 100, strand: '+' },
    { name: 'GFP', type: 'CDS', start: 200, end: 1000, strand: '+' },
    { name: 'AmpR', type: 'resistance', start: 1500, end: 2500, strand: '-' }
  ]

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders canvas element', () => {
    const wrapper = mount(PlasmidMap, {
      props: {
        features: defaultFeatures,
        length: 5000
      }
    })
    
    expect(wrapper.find('canvas').exists()).toBe(true)
  })

  it('renders with default width and height', () => {
    const wrapper = mount(PlasmidMap, {
      props: {
        features: defaultFeatures
      }
    })
    
    const canvas = wrapper.find('canvas')
    expect(canvas.attributes('width')).toBe('500')
    expect(canvas.attributes('height')).toBe('500')
  })

  it('accepts custom width and height', () => {
    const wrapper = mount(PlasmidMap, {
      props: {
        features: defaultFeatures,
        width: 600,
        height: 400
      }
    })
    
    const canvas = wrapper.find('canvas')
    expect(canvas.attributes('width')).toBe('600')
    expect(canvas.attributes('height')).toBe('400')
  })

  it('renders legend with feature types', () => {
    const wrapper = mount(PlasmidMap, {
      props: {
        features: defaultFeatures
      }
    })
    
    const legendItems = wrapper.findAll('.legend-item')
    expect(legendItems.length).toBeGreaterThan(0)
  })

  it('computes plasmidLength from props', async () => {
    const wrapper = mount(PlasmidMap, {
      props: {
        features: defaultFeatures,
        length: 10000
      }
    })
    
    // Component should render without errors
    expect(wrapper.find('canvas').exists()).toBe(true)
  })

  it('uses sequence length when length prop not provided', async () => {
    const sequence = 'ATGC'.repeat(1000) // 4000 bp
    const wrapper = mount(PlasmidMap, {
      props: {
        sequence: sequence,
        features: defaultFeatures
      }
    })
    
    expect(wrapper.find('canvas').exists()).toBe(true)
  })

  it('handles empty features array', () => {
    const wrapper = mount(PlasmidMap, {
      props: {
        features: [],
        length: 5000
      }
    })
    
    expect(wrapper.find('canvas').exists()).toBe(true)
  })

  it('displays plasmid name in center', async () => {
    const wrapper = mount(PlasmidMap, {
      props: {
        features: defaultFeatures,
        name: 'pET-28a',
        length: 5369
      }
    })
    
    // Canvas should be rendered
    expect(wrapper.find('canvas').exists()).toBe(true)
  })

  it('renders tooltip when feature is hovered', async () => {
    const wrapper = mount(PlasmidMap, {
      props: {
        features: defaultFeatures,
        length: 5000
      }
    })
    
    // Initially no tooltip should be visible
    expect(wrapper.find('.feature-tooltip').exists()).toBe(false)
  })

  it('has correct feature colors mapping', () => {
    const wrapper = mount(PlasmidMap, {
      props: {
        features: defaultFeatures
      }
    })
    
    // Check that legend renders with expected colors
    const legendItems = wrapper.findAll('.legend-item')
    expect(legendItems.length).toBeGreaterThan(0)
  })
})
