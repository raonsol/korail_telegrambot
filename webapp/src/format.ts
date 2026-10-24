import type { Reservation, ReservationStatus, SeatType } from './api'

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토']

export function formatDate(yyyymmdd: string): string {
  const y = Number(yyyymmdd.slice(0, 4))
  const m = Number(yyyymmdd.slice(4, 6))
  const d = Number(yyyymmdd.slice(6, 8))
  const weekday = WEEKDAYS[new Date(y, m - 1, d).getDay()]
  return `${m}월 ${d}일 (${weekday})`
}

export function formatTime(hhmm: string): string {
  return `${hhmm.slice(0, 2)}:${hhmm.slice(2, 4)}`
}

export function timeRange(r: Pick<Reservation, 'dep_time' | 'max_dep_time'>): string {
  return `${formatTime(r.dep_time)} ~ ${formatTime(r.max_dep_time)}`
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return '-'
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(iso))
}

export function relativeTime(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60) return '방금 전'
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`
  if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`
  return `${Math.floor(diff / 86400)}일 전`
}

export const STATUS_LABELS: Record<ReservationStatus, string> = {
  queued: '대기 중',
  running: '좌석 찾는 중',
  success: '예약 성공',
  failed: '예약 실패',
  error: '오류',
  cancelled: '취소됨',
}

/** 한국 시간 기준 날짜(YYYY-MM-DD)와 시각(HH:MM) — 서버 검증과 동일한 기준 */
export function kstNow(offsetDays = 0): { date: string; time: string } {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(Date.now() + offsetDays * 86400_000))
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '00'
  return {
    date: `${get('year')}-${get('month')}-${get('day')}`,
    time: `${get('hour')}:${get('minute')}`,
  }
}

export function compactToIso(yyyymmdd: string): string {
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`
}

/** 입력 중인 전화번호에 하이픈 추가 */
export function formatPhoneInput(value: string): string {
  const digits = value.replace(/\D/g, '').slice(0, 11)
  if (digits.length < 4) return digits
  if (digits.length < 8) return `${digits.slice(0, 3)}-${digits.slice(3)}`
  return `${digits.slice(0, 3)}-${digits.slice(3, 7)}-${digits.slice(7)}`
}

/** 화면 색을 정하는 상태 (성공했지만 코레일에서 취소한 예약은 취소 색) */
export function displayStatus(r: Reservation): ReservationStatus {
  return r.status === 'success' && r.korail_cancelled_at ? 'cancelled' : r.status
}

export const KORAIL_PAYMENT_URL =
  'https://www.korail.com/ticket/reservation/list'

/** 좌석 선택 (일반실/특실 체크 + 둘 다 고른 경우 우선순위) ↔ 서버의 seat_type */
export interface SeatSelection {
  general: boolean
  special: boolean
  priority: 'general' | 'special'
}

export function seatSelectionFrom(seatType: SeatType): SeatSelection {
  return {
    general: seatType !== 'special_only',
    special: seatType !== 'general_only',
    priority: seatType === 'special' || seatType === 'special_only' ? 'special' : 'general',
  }
}

/** 하나도 고르지 않았으면 null */
export function toSeatType(seat: SeatSelection): SeatType | null {
  if (seat.general && seat.special) return seat.priority === 'general' ? 'general' : 'special'
  if (seat.general) return 'general_only'
  if (seat.special) return 'special_only'
  return null
}

export const SEAT_SUMMARY: Record<SeatType, string> = {
  general: '일반실·특실 (일반실 우선)',
  special: '일반실·특실 (특실 우선)',
  general_only: '일반실만',
  special_only: '특실만',
}

/** 관리자 표처럼 좁은 곳에 쓰는 좌석 조건 */
export const SEAT_SHORT: Record<SeatType, string> = {
  general: '일반실 우선',
  special: '특실 우선',
  general_only: '일반실만',
  special_only: '특실만',
}

/** 정각 단위 시각 (24는 그날 마지막 열차까지 → 23:59) */
/** 하루의 마지막 (출발 시각 범위의 끝 24:00 = 23:59) */
export const DAY_END = 24 * 60

/** 0시부터 지난 분 → HH:MM (DAY_END는 23:59) */
export function minuteLabel(minutes: number): string {
  if (minutes >= DAY_END) return '23:59'
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
}

/** 0시부터 지난 분 → HHMM (API 형식, DAY_END는 2359) */
export function minuteToHhmm(minutes: number): string {
  return minuteLabel(minutes).replace(':', '')
}

/** HHMM 또는 HH:MM → 0시부터 지난 분 */
export function hhmmToMinute(hhmm: string): number {
  const digits = hhmm.replace(':', '')
  return Number(digits.slice(0, 2)) * 60 + Number(digits.slice(2, 4))
}

/** 14:52 → 오후 2:52 */
function meridiemTime(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number)
  return `${h < 12 ? '오전' : '오후'} ${h % 12 || 12}:${String(m).padStart(2, '0')}`
}

export interface ResultInfo {
  trainNo?: string // "KTX 015"
  depTime?: string
  arrTime?: string
  fare?: string // "59,800원 · 1석"
  payBy?: string // "오후 2:52"
  depStation?: string // 실제 출발역 (인근역 포함 예약은 고른 역과 다를 수 있음)
  arrStation?: string
}

/**
 * 예약 결과 문구(pykorail Reservation 표기)에서 표시용 정보를 뽑음. 못 읽으면 빈 값
 * 예: "[KTX 015]  10/12 07:30~10:12  서울~부산 (2시간 42분), 59800원(1석), 구입기한 10월 5일 14:52"
 */
export function parseResultText(text: string | null): ResultInfo {
  if (!text) return {}
  const train = text.match(/\[([^\]\d]*?)\s*(\d+)\]/)
  const times = text.match(/(\d{2}:\d{2})~(\d{2}:\d{2})/)
  const fare = text.match(/([\d,]+)원\((\d+)석\)/)
  const limit = text.match(/구입기한[^,]*?(\d{1,2}:\d{2})/)
  // 시각 뒤의 "출발역~도착역" (인근역 포함 예약은 고른 역과 다를 수 있음)
  const stations = text.match(/\d{2}:\d{2}~\d{2}:\d{2}\s+(\S+)~(\S+)/)
  return {
    depStation: stations?.[1],
    arrStation: stations?.[2],
    trainNo: train ? `${train[1].trim()} ${train[2]}`.trim() : undefined,
    depTime: times?.[1],
    arrTime: times?.[2],
    fare: fare ? `${Number(fare[1].replace(/,/g, '')).toLocaleString()}원 · ${fare[2]}석` : undefined,
    payBy: limit ? meridiemTime(limit[1]) : undefined,
  }
}
