import type { SeatSelection } from '../format'
import { Segmented } from './Segmented'

const PRIORITY_OPTIONS: { value: SeatSelection['priority']; label: string; hint: string }[] = [
  { value: 'general', label: '일반실 먼저', hint: '없으면 특실' },
  { value: 'special', label: '특실 먼저', hint: '없으면 일반실' },
]

/** 일반실/특실을 각각 켜고 끄고, 둘 다 켜면 어느 쪽을 먼저 예약할지 고름 */
export function SeatPicker({
  value,
  onChange,
}: {
  value: SeatSelection
  onChange: (value: SeatSelection) => void
}) {
  const toggle = (key: 'general' | 'special') => onChange({ ...value, [key]: !value[key] })
  const both = value.general && value.special
  const none = !value.general && !value.special

  return (
    <>
      <div className="segmented" role="group" aria-label="좌석" style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
        {(['general', 'special'] as const).map((key) => (
          <button
            key={key}
            type="button"
            role="checkbox"
            aria-checked={value[key]}
            className={`seat-toggle ${value[key] ? 'selected' : ''}`}
            onClick={() => toggle(key)}
          >
            <span>
              <span className="seat-check" aria-hidden="true">
                {value[key] ? '✓' : ''}
              </span>
              {key === 'general' ? '일반실' : '특실'}
            </span>
          </button>
        ))}
      </div>
      {both && (
        <div className="seat-priority">
          <span className="label small">둘 다 있으면</span>
          <Segmented
            name="우선 예약할 좌석"
            value={value.priority}
            options={PRIORITY_OPTIONS}
            onChange={(priority) => onChange({ ...value, priority })}
          />
        </div>
      )}
      <small className={none ? 'field-error' : 'muted'}>
        {none
          ? '일반실과 특실 중 하나 이상 선택해주세요.'
          : both
            ? `${value.priority === 'general' ? '일반실' : '특실'}을 먼저 찾고, 없으면 ${value.priority === 'general' ? '특실' : '일반실'}을 예약합니다.`
            : `${value.general ? '일반실' : '특실'}만 예약합니다.`}
      </small>
    </>
  )
}
