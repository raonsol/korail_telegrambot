export function Switch({
  checked,
  disabled,
  onChange,
  label,
}: {
  checked: boolean
  disabled?: boolean
  onChange: () => void
  label: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={`kb-switch${checked ? ' is-on' : ''}`}
      disabled={disabled}
      onClick={onChange}
    >
      <span />
    </button>
  )
}
