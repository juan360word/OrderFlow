type Props = {
  value: number
  min?: number
  max?: number
  onChange: (val: number) => void
}

export function Stepper({ value, min = 1, max = 10_000, onChange }: Props) {
  return (
    <div className="stepper">
      <button
        type="button"
        className="stepper-btn"
        onClick={() => onChange(Math.max(min, value - 1))}
        disabled={value <= min}
        aria-label="Reducir cantidad"
      >
        −
      </button>
      <span className="stepper-val">{value}</span>
      <button
        type="button"
        className="stepper-btn"
        onClick={() => onChange(Math.min(max, value + 1))}
        disabled={value >= max}
        aria-label="Aumentar cantidad"
      >
        +
      </button>
    </div>
  )
}
