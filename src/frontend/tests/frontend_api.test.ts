import { describe, it, expect, vi, beforeEach } from 'vitest'
import axios from 'axios'

// Mock axios
vi.mock('axios', () => {
  const mockGet = vi.fn()
  const mockPost = vi.fn()
  const mockDelete = vi.fn()
  const mockInstance = {
    get: mockGet,
    post: mockPost,
    delete: mockDelete,
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() }
    }
  }
  
  return {
    default: {
      create: vi.fn(() => mockInstance)
    }
  }
})

// Import after mock is set up
import {
  submitDesign,
  getDesign,
  getVectors,
  getVector,
  getCodonTables,
  login,
  register,
  submitBatchDesign,
  getBatchProgress
} from '@/api'

describe('API Functions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  describe('submitDesign', () => {
    it('is defined', () => {
      expect(submitDesign).toBeDefined()
      expect(typeof submitDesign).toBe('function')
    })
  })

  describe('getDesign', () => {
    it('is defined', () => {
      expect(getDesign).toBeDefined()
      expect(typeof getDesign).toBe('function')
    })
  })

  describe('getVectors', () => {
    it('is defined', () => {
      expect(getVectors).toBeDefined()
      expect(typeof getVectors).toBe('function')
    })
  })

  describe('getVector', () => {
    it('is defined', () => {
      expect(getVector).toBeDefined()
      expect(typeof getVector).toBe('function')
    })
  })

  describe('getCodonTables', () => {
    it('is defined', () => {
      expect(getCodonTables).toBeDefined()
      expect(typeof getCodonTables).toBe('function')
    })
  })

  describe('login', () => {
    it('is defined', () => {
      expect(login).toBeDefined()
      expect(typeof login).toBe('function')
    })
  })

  describe('register', () => {
    it('is defined', () => {
      expect(register).toBeDefined()
      expect(typeof register).toBe('function')
    })
  })

  describe('submitBatchDesign', () => {
    it('is defined', () => {
      expect(submitBatchDesign).toBeDefined()
      expect(typeof submitBatchDesign).toBe('function')
    })
  })

  describe('getBatchProgress', () => {
    it('is defined', () => {
      expect(getBatchProgress).toBeDefined()
      expect(typeof getBatchProgress).toBe('function')
    })
  })
})

describe('Local Storage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('can store and retrieve token', () => {
    localStorage.setItem('token', 'test-token')
    expect(localStorage.getItem('token')).toBe('test-token')
  })

  it('can store and retrieve user', () => {
    const user = { id: 1, email: 'test@example.com', username: 'testuser' }
    localStorage.setItem('user', JSON.stringify(user))
    
    const stored = localStorage.getItem('user')
    expect(stored).toBeDefined()
    expect(JSON.parse(stored!)).toEqual(user)
  })

  it('can clear storage', () => {
    localStorage.setItem('token', 'test-token')
    localStorage.setItem('user', JSON.stringify({ id: 1 }))
    
    localStorage.clear()
    
    expect(localStorage.getItem('token')).toBeNull()
    expect(localStorage.getItem('user')).toBeNull()
  })
})
