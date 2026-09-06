import type { OrderStatus } from '../lib/schemas'

const STATUS_MAP: Record<OrderStatus, { label: string; cls: string }> = {
  pending: { label: 'Pendiente', cls: 'badge-warn' },
  confirmed: { label: 'Confirmado', cls: 'badge-ok' },
  cancelled: { label: 'Cancelado', cls: 'badge-bad' },
}

export function StatusBadge({ status }: { status: OrderStatus }) {
  const { label, cls } = STATUS_MAP[status] ?? { label: status, cls: 'badge-ink' }
  return <span className={`badge ${cls}`}>{label}</span>
}

type StockBadgeProps = {
  available: number
  label?: string
}

export function StockBadge({ available, label }: StockBadgeProps) {
  if (available <= 0)
    return <span className="badge badge-bad">Sin stock</span>
  if (available <= 5)
    return <span className="badge badge-warn">{label ?? `Últimas ${available} u.`}</span>
  return <span className="badge badge-ok">{label ?? 'Disponible'}</span>
}
