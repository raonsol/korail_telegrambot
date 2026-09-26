// 백엔드 API 클라이언트 (세션 쿠키 + CSRF 헤더)

export type ReservationStatus =
  | 'queued'
  | 'running'
  | 'success'
  | 'failed'
  | 'error'
  | 'cancelled'

export type TrainType = 'KTX' | 'ALL'
export type SeatType = 'general' | 'general_only' | 'special' | 'special_only'

export interface Reservation {
  id: string
  owner_id: string
  origin: 'telegram' | 'web'
  status: ReservationStatus
  dep_date: string // YYYYMMDD
  src_station: string
  dst_station: string
  dep_time: string // HHMM
  max_dep_time: string // HHMM
  train_type: TrainType
  seat_type: SeatType
  train_type_label: string
  seat_type_label: string
  attempts: number
  result_text: string | null
  error: string | null
  created_at: string
  updated_at: string
  finished_at: string | null
  is_active: boolean
}

export interface ReservationInput {
  dep_date: string // YYYY-MM-DD
  src_station: string
  dst_station: string
  dep_time: string
  max_dep_time: string
  train_type: TrainType
  seat_type: SeatType
}

export interface Me {
  user: {
    id: string
    phone: string | null
    name: string | null
    is_admin: boolean
    telegram_linked: boolean
    telegram_notify: boolean
  }
  csrf_token: string
  push_enabled: boolean
  push_public_key: string | null
}

export interface Station {
  code: string
  name: string
}

export interface StationResult {
  stations: Station[]
  total: number
  page: number
  page_size: number
}

export interface AdminUser {
  id: string
  phone: string
  name: string | null
  is_active: boolean
  telegram_linked: boolean
  telegram_notify: boolean
  created_at: string
  last_login_at: string | null
}

export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

let csrfToken: string | null = null

export function setCsrfToken(token: string | null) {
  csrfToken = token
}

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE'

export async function api<T>(
  path: string,
  options: { method?: Method; body?: unknown } = {},
): Promise<T> {
  const method = options.method ?? 'GET'
  const headers: Record<string, string> = {}
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken

  let response: Response
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    })
  } catch {
    throw new ApiError(0, 'NETWORK', '서버에 연결할 수 없습니다. 네트워크를 확인해주세요.')
  }

  if (response.status === 204) return undefined as T
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    throw new ApiError(
      response.status,
      data?.code ?? 'ERROR',
      data?.message ?? '요청을 처리하지 못했습니다.',
    )
  }
  return data as T
}

export const Api = {
  me: () => api<Me>('/api/auth/me'),
  login: (phone: string, password: string, remember: boolean) =>
    api<Me>('/api/auth/login', { method: 'POST', body: { phone, password, remember } }),
  adminLogin: (password: string, remember: boolean) =>
    api<Me>('/api/auth/admin-login', { method: 'POST', body: { password, remember } }),
  logout: () => api<void>('/api/auth/logout', { method: 'POST' }),
  updateSettings: (settings: { telegram_notify?: boolean }) =>
    api<Me>('/api/auth/me/settings', { method: 'PATCH', body: settings }),

  reservations: (status: 'active' | 'history' | 'all', scope: 'mine' | 'all' = 'mine') =>
    api<Reservation[]>(`/api/reservations?status=${status}&scope=${scope}`),
  reservation: (id: string) => api<Reservation>(`/api/reservations/${encodeURIComponent(id)}`),
  createReservation: (input: ReservationInput) =>
    api<Reservation>('/api/reservations', { method: 'POST', body: input }),
  cancelReservation: (id: string) =>
    api<Reservation>(`/api/reservations/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  cancelAll: (scope: 'mine' | 'all' = 'mine') =>
    api<Reservation[]>(`/api/reservations?scope=${scope}`, { method: 'DELETE' }),

  stations: (q: string, page = 1) =>
    api<StationResult>(`/api/stations?q=${encodeURIComponent(q)}&page=${page}`),

  subscribePush: (subscription: PushSubscriptionJSON) =>
    api<void>('/api/push/subscriptions', { method: 'POST', body: subscription }),
  unsubscribePush: (endpoint: string) =>
    api<void>('/api/push/unsubscribe', { method: 'POST', body: { endpoint } }),

  users: () => api<AdminUser[]>('/api/admin/users'),
  createUser: (phone: string, name: string) =>
    api<AdminUser>('/api/admin/users', { method: 'POST', body: { phone, name: name || null } }),
  updateUser: (id: string, patch: { name?: string; is_active?: boolean }) =>
    api<AdminUser>(`/api/admin/users/${id}`, { method: 'PATCH', body: patch }),
  deleteUser: (id: string) => api<void>(`/api/admin/users/${id}`, { method: 'DELETE' }),
}
