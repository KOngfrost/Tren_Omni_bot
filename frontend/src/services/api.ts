import type { Department, SystemStats, TicketSummary, User } from '../types'

let cachedCsrfToken = ''

export function setCsrfToken(token: string) {
  cachedCsrfToken = token
}

export function getCsrfToken(): string {
  return cachedCsrfToken
}

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers || {})
  
  if (!headers.has('Content-Type') && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }

  if (cachedCsrfToken && !headers.has('X-CSRF-Token')) {
    headers.set('X-CSRF-Token', cachedCsrfToken)
  }

  const response = await fetch(endpoint, {
    ...options,
    headers,
    credentials: 'include', // передача cookies сессии
  })

  if (!response.ok) {
    let errorDetail = `Ошибка запроса: ${response.status}`
    try {
      const errorJson = await response.json()
      if (errorJson.detail) {
        errorDetail = errorJson.detail
      } else if (errorJson.error) {
        errorDetail = errorJson.error
      }
    } catch {
      // response не JSON
    }
    throw new Error(errorDetail)
  }

  return response.json()
}

export const api = {
  // Auth
  async getMe(): Promise<{ authenticated: boolean; user: User | null; csrf_token: string }> {
    const data = await request<{ authenticated: boolean; user: User | null; csrf_token: string }>('/api/v1/auth/me')
    if (data.csrf_token) {
      setCsrfToken(data.csrf_token)
    }
    return data
  },

  async login(username: string, password: string): Promise<{ success: boolean; needs_2fa?: boolean; masked_vk_id?: string; user?: User; csrf_token?: string }> {
    const data = await request<{ success: boolean; needs_2fa?: boolean; masked_vk_id?: string; user?: User; csrf_token?: string }>(
      '/api/v1/auth/login',
      {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      }
    )
    if (data.csrf_token) setCsrfToken(data.csrf_token)
    return data
  },

  async verify2FA(code: string): Promise<{ success: boolean; user: User; csrf_token: string }> {
    const data = await request<{ success: boolean; user: User; csrf_token: string }>('/api/v1/auth/2fa', {
      method: 'POST',
      body: JSON.stringify({ code }),
    })
    if (data.csrf_token) setCsrfToken(data.csrf_token)
    return data
  },

  async loginWithTelegram(initData: string): Promise<{ success: boolean; user: User; csrf_token: string }> {
    const data = await request<{ success: boolean; user: User; csrf_token: string }>('/api/v1/auth/telegram-webapp', {
      method: 'POST',
      body: JSON.stringify({ init_data: initData }),
    })
    if (data.csrf_token) setCsrfToken(data.csrf_token)
    return data
  },

  async logout(): Promise<{ success: boolean }> {
    return request<{ success: boolean }>('/api/v1/auth/logout', { method: 'POST' })
  },

  // Stats & Health
  async getStats(): Promise<SystemStats> {
    return request<SystemStats>('/api/v1/stats')
  },

  // Departments
  async getDepartments(): Promise<Department[]> {
    return request<Department[]>('/api/v1/departments/')
  },

  async createDepartment(name: string): Promise<Department> {
    return request<Department>('/api/v1/departments/', {
      method: 'POST',
      body: JSON.stringify({ name }),
    })
  },

  async renameDepartment(id: number, name: string): Promise<Department> {
    return request<Department>(`/api/v1/departments/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ name }),
    })
  },

  async deleteDepartment(id: number): Promise<{ id: number; deleted: boolean }> {
    return request<{ id: number; deleted: boolean }>(`/api/v1/departments/${id}`, {
      method: 'DELETE',
    })
  },

  // Tickets
  async getTickets(limit = 50): Promise<TicketSummary[]> {
    return request<TicketSummary[]>(`/api/v1/tickets/?limit=${limit}`)
  },
}
