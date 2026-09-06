import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { ordersApi, productsApi } from '../lib/api'
import { Layout } from '../components/Layout'
import { KpiSkeleton } from '../components/Skeleton'
import type { OrderStatus } from '../lib/schemas'

const STATUS_COLORS: Record<OrderStatus, string> = {
  pending: 'var(--warn)',
  confirmed: 'var(--ok)',
  cancelled: 'var(--bad)',
}

const STATUS_LABELS: Record<OrderStatus, string> = {
  pending: 'Pendiente',
  confirmed: 'Confirmado',
  cancelled: 'Cancelado',
}

export function AdminMetricsScreen() {
  const { data: ordersData, isLoading: loadingOrders } = useQuery({
    queryKey: ['orders', undefined],
    queryFn: () => ordersApi.list({ limit: 100 }),
  })

  const { data: productsData, isLoading: loadingProducts } = useQuery({
    queryKey: ['products-admin'],
    queryFn: () => productsApi.list({ limit: 200 }),
  })

  const orders = ordersData?.items ?? []
  const products = productsData?.items ?? []
  const isLoading = loadingOrders || loadingProducts

  // Derived metrics
  const totalOrders = ordersData?.total ?? 0
  const totalProducts = productsData?.total ?? 0
  const revenue = orders
    .filter((o) => o.status === 'confirmed')
    .reduce((sum, o) => sum + parseFloat(o.total_amount), 0)

  const byStatus = orders.reduce<Record<string, number>>((acc, o) => {
    acc[o.status] = (acc[o.status] ?? 0) + 1
    return acc
  }, {})

  const allStatuses: OrderStatus[] = ['pending', 'confirmed', 'cancelled']
  const maxCount = Math.max(...Object.values(byStatus), 1)

  const kpis = [
    {
      label: 'Pedidos totales',
      value: totalOrders,
      hint: 'Todos los estados',
    },
    {
      label: 'Ingresos confirmados',
      value: `$${revenue.toFixed(2)}`,
      hint: 'Solo pedidos confirmados',
    },
    {
      label: 'Productos activos',
      value: products.filter((p) => p.is_active).length,
      hint: `de ${totalProducts} en total`,
    },
    {
      label: 'Tasa de cancelación',
      value:
        totalOrders > 0
          ? `${(((byStatus.cancelled ?? 0) / totalOrders) * 100).toFixed(1)}%`
          : '—',
      hint: 'Cancelados / total',
    },
  ]

  return (
    <Layout kicker="Administración" title="Métricas">
      {/* KPI grid */}
      {isLoading ? (
        <div className="kpi-grid" style={{ marginBottom: 28 }}>
          {Array.from({ length: 4 }).map((_, i) => <KpiSkeleton key={i} />)}
        </div>
      ) : (
        <div className="kpi-grid">
          {kpis.map((k, i) => (
            <motion.div
              key={k.label}
              className="kpi-card"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2, delay: i * 0.05 }}
            >
              <p className="kpi-label">{k.label}</p>
              <p className="kpi-value mono">{k.value}</p>
              <p className="kpi-hint">{k.hint}</p>
            </motion.div>
          ))}
        </div>
      )}

      {/* Bar chart by status */}
      <div className="card">
        <h3 style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink)', marginBottom: 20 }}>
          Pedidos por estado
        </h3>
        <div style={{ display: 'flex', gap: 24, alignItems: 'flex-end', height: 140 }}>
          {allStatuses.map((status) => {
            const count = byStatus[status] ?? 0
            const pct = maxCount > 0 ? (count / maxCount) * 100 : 0

            return (
              <div
                key={status}
                style={{
                  flex: 1,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: 8,
                  height: '100%',
                  justifyContent: 'flex-end',
                }}
              >
                <p
                  className="mono"
                  style={{ fontSize: 18, fontWeight: 600, color: 'var(--ink)' }}
                >
                  {count}
                </p>
                <motion.div
                  style={{
                    width: '100%',
                    maxWidth: 80,
                    background: STATUS_COLORS[status],
                    borderRadius: '8px 8px 0 0',
                    boxShadow: 'var(--sh-btn)',
                    minHeight: 4,
                  }}
                  initial={{ height: 0 }}
                  animate={{ height: `${Math.max(pct, 4)}%` }}
                  transition={{ duration: 0.24, ease: 'easeOut' }}
                />
                <p
                  style={{
                    fontSize: 12,
                    fontWeight: 700,
                    color: STATUS_COLORS[status],
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                  }}
                >
                  {STATUS_LABELS[status]}
                </p>
              </div>
            )
          })}
        </div>
      </div>
    </Layout>
  )
}
