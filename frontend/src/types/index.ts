export interface User {
  username: string
  role: 'SUPERADMIN' | 'DEPARTMENT_ADMIN' | string
  web_user_id?: number | null
  department_id?: number | null
  bootstrap?: boolean
  telegram_id?: number
}

export interface DepartmentUsage {
  tickets?: number
  knowledge?: number
  faq?: number
  events?: number
  subscriptions?: number
  admins?: number
}

export interface Department {
  id: number
  name: string
  created_at?: string | null
  usage?: DepartmentUsage
}

export interface TicketSummary {
  id: number
  topic: string
  status: string
  department_id?: number | null
  department_name?: string | null
  created_at?: string | null
}

export interface SystemStats {
  version: string
  environment: string
  redis_connected: boolean
  sentry_enabled: boolean
  two_factor_enabled: boolean
  total_tickets: number
  active_tickets: number
}
