interface Option<T extends string> {
  value: T
  label: string
  hint?: string
}

export function Segmented<T extends string>({
  name,
  value,
  options,
  onChange,
  columns = options.length,
}: {
  name: string
  value: T
  options: Option<T>[]
  onChange: (value: T) => void
  columns?: number
}) {
  return (
    <div
      className="segmented"
      role="radiogroup"
      aria-label={name}
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          className={value === option.value ? 'selected' : ''}
          onClick={() => onChange(option.value)}
        >
          <span>{option.label}</span>
          {option.hint && <small>{option.hint}</small>}
        </button>
      ))}
    </div>
  )
}
