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
  /** 예약 생성 시 고른 예약대기 사용 여부 */
  allow_waitlist: boolean
  /** 인근역 출발·도착 열차도 예약 (코레일 앱의 "인접역") */
  include_nearby: boolean
  attempts: number
  result_text: string | null
  /** 좌석 대신 예약대기를 신청함 (status === 'success') */
  waitlisted: boolean
  error: string | null
  /** 예약 후 관리: 코레일 예약번호, 결제 기한, 코레일에서 취소한 시각 */
  korail_rsv_id: string | null
  pay_deadline: string | null
  korail_cancelled_at: string | null
  /** 이 앱에서 코레일 예약을 취소할 수 있음 */
  korail_cancellable: boolean
  created_at: string
  updated_at: string
  finished_at: string | null
  is_active: boolean
}

export interface Train {
  train_no: string // "015"
  train_type: string // "KTX", "KTX-산천" ...
  dep_time: string // HHMM
  arr_time: string // HHMM
  duration_minutes: number | null
  general_seat: boolean
  special_seat: boolean
  /** 매진이지만 (일반실) 예약대기를 받음 */
  waitlist: boolean
  /** 실제 출발·도착역 (인근역 포함 조회에서는 고른 역과 다를 수 있음) */
  dep_station: string | null
  arr_station: string | null
}

export interface TrainSearch {
  trains: Train[]
  /** 시간대 끝까지 조회하지 못함 (뒤쪽 열차가 더 있을 수 있음) */
  truncated: boolean
}

export interface TrainQuery {
  date: string // YYYY-MM-DD
  src: string
  dst: string
  start: string // HHMM, 이 시각 이후 출발
  end: string // HHMM, 이 시각 전 출발
  train_type: TrainType
  /** 'true' 이면 인근역 출발·도착 열차도 */
  nearby: 'true' | 'false'
}

export interface ReservationInput {
  dep_date: string // YYYY-MM-DD
  src_station: string
  dst_station: string
  dep_time: string
  max_dep_time: string
  train_type: TrainType
  seat_type: SeatType
  allow_waitlist: boolean
  include_nearby: boolean
}

/** 코레일 계정의 결제 전 예약 (예약대기 포함) - "내 승차권" */
export interface KorailReservation {
  rsv_id: string
  train_no: string
  train_type: string
  dep_date: string // YYYYMMDD
  dep_time: string // HHMM
  arr_time: string // HHMM
  dep_station: string
  arr_station: string
  price: number
  seat_count: number
  /** 좌석이 아니라 예약대기 (결제 기한 없음) */
  waiting: boolean
  pay_deadline: string | null
  /** 코레일봇이 잡은 예약이면 그 예약 기록 id */
  reservation_id: string | null
}

/** 코레일 계정의 발권된 승차권 */
export interface KorailTicket {
  rsv_id: string
  train_no: string
  train_type: string
  dep_date: string
  dep_time: string
  arr_time: string
  dep_station: string
  arr_station: string
  car_no: string
  seats: string // "12A" 또는 "12A~12B"
  seat_count: number
  price: number
  reservation_id: string | null
}

export interface KorailAccount {
  korail_id: string
  reservations: KorailReservation[]
  tickets: KorailTicket[]
  fetched_at: string
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

/** 관리자 운영 설정 (core/app_settings.py) */
export interface AdminSetting {
  key: string
  group: string
  label: string
  unit: string
  help: string
  value: number
  /** 환경변수·코드 기본값 */
  default: number
  min: number
  max: number
  /** 1이면 정수만 */
  step: number
  /** 관리자가 바꾼 값인지 */
  overridden: boolean
}

/** 보낸 항목만 바뀜. telegram_linked는 false(연결 해제)만 가능 */
export interface AdminUserPatch {
  name?: string
  is_active?: boolean
  telegram_notify?: boolean
  telegram_linked?: false
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

  reservations: (
    status: 'active' | 'history' | 'all',
    scope: 'mine' | 'all' = 'mine',
    limit = 100,
  ) => api<Reservation[]>(`/api/reservations?status=${status}&scope=${scope}&limit=${limit}`),
  reservation: (id: string) => api<Reservation>(`/api/reservations/${encodeURIComponent(id)}`),
  createReservation: (input: ReservationInput) =>
    api<Reservation>('/api/reservations', { method: 'POST', body: input }),
  cancelReservation: (id: string) =>
    api<Reservation>(`/api/reservations/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  cancelKorailReservation: (id: string) =>
    api<Reservation>(`/api/reservations/${encodeURIComponent(id)}/korail-cancel`, {
      method: 'POST',
    }),
  korailAccount: (refresh = false) =>
    api<KorailAccount>(`/api/korail/account${refresh ? '?refresh=true' : ''}`),
  cancelKorailRsv: (rsvId: string) =>
    api<KorailAccount>(`/api/korail/reservations/${encodeURIComponent(rsvId)}/cancel`, {
      method: 'POST',
    }),
  cancelAll: (scope: 'mine' | 'all' = 'mine') =>
    api<Reservation[]>(`/api/reservations?scope=${scope}`, { method: 'DELETE' }),

  stations: (q: string, page = 1) =>
    api<StationResult>(`/api/stations?q=${encodeURIComponent(q)}&page=${page}`),

  trains: (q: TrainQuery) => api<TrainSearch>(`/api/trains?${new URLSearchParams({ ...q })}`),

  subscribePush: (subscription: PushSubscriptionJSON) =>
    api<void>('/api/push/subscriptions', { method: 'POST', body: subscription }),
  unsubscribePush: (endpoint: string) =>
    api<void>('/api/push/unsubscribe', { method: 'POST', body: { endpoint } }),

  users: () => api<AdminUser[]>('/api/admin/users'),
  createUser: (phone: string, name: string) =>
    api<AdminUser>('/api/admin/users', { method: 'POST', body: { phone, name: name || null } }),
  updateUser: (id: string, patch: AdminUserPatch) =>
    api<AdminUser>(`/api/admin/users/${id}`, { method: 'PATCH', body: patch }),
  deleteUser: (id: string) => api<void>(`/api/admin/users/${id}`, { method: 'DELETE' }),

  adminSettings: () => api<AdminSetting[]>('/api/admin/settings'),
  /** null이면 기본값으로 되돌림 */
  updateAdminSettings: (values: Record<string, number | null>) =>
    api<AdminSetting[]>('/api/admin/settings', { method: 'PATCH', body: { values } }),
}
