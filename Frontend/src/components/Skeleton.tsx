import type { CSSProperties } from 'react'

type Props = {
  width?: string | number
  height?: string | number
  radius?: number
  className?: string
  style?: CSSProperties
}

export function Skeleton({ width = '100%', height = 16, radius = 8, className, style }: Props) {
  return (
    <div
      className={`skeleton${className ? ` ${className}` : ''}`}
      style={{ width, height, borderRadius: radius, ...style }}
    />
  )
}

export function ProductCardSkeleton() {
  return (
    <div className="product-card" style={{ gap: 0 }}>
      <div className="product-thumb" />
      <div className="product-body" style={{ gap: 10 }}>
        <Skeleton height={18} radius={6} />
        <Skeleton height={14} width="60%" radius={6} />
        <Skeleton height={36} radius={14} style={{ marginTop: 'auto' }} />
      </div>
    </div>
  )
}

export function OrderRowSkeleton() {
  return (
    <tr>
      <td colSpan={5}>
        <Skeleton height={44} radius={8} />
      </td>
    </tr>
  )
}

export function KpiSkeleton() {
  return (
    <div className="kpi-card">
      <Skeleton height={11} width="50%" />
      <Skeleton height={30} width="70%" radius={6} />
      <Skeleton height={12} width="40%" />
    </div>
  )
}
