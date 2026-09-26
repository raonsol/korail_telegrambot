import type { Reservation, ReservationStatus } from './api'

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
    hour: '2-digit',
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

/** HH:MM을 다음 10분 단위로 올림 (최대 23:50) */
export function roundUpTime(time: string): string {
  const [h, m] = time.split(':').map(Number)
  const total = Math.min(Math.ceil((h * 60 + m + 1) / 10) * 10, 23 * 60 + 50)
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
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

export const KORAIL_PAYMENT_URL =
  'https://www.korail.com/ticket/reservation/list'
