import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ordersApi } from '../lib/api'
import { useAuth } from '../store/auth'
import { Layout } from '../components/Layout'
import { StatusBadge } from '../components/StatusBadge'
import { OrderRowSkeleton } from '../components/Skeleton'
import type { OrderStatus } from '../lib/schemas'

const FILTERS: { label: string; value: OrderStatus | 'all' }[] = [
  { label: 'Todos', value: 'all' },
  { label: 'Pendiente', value: 'pending' },
  { label: 'Confirmado', value: 'confirmed' },
  { label: 'Cancelado', value: 'cancelled' },
]

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('es-MX', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

export function OrdersScreen() {
  const [filter, setFilter] = useState<OrderStatus | 'all'>('all')
  const navigate = useNavigate()
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'

  const { data, isLoading } = useQuery({
    queryKey: ['orders', filter],
    queryFn: () =>
      ordersApi.list({ status: filter === 'all' ? undefined : filter, limit: 50 }),
  })

  const orders = data?.items ?? []

  const title = isAdmin ? 'Todos los pedidos' : 'Mis pedidos'
  const kicker = isAdmin ? 'Administración' : 'Historial'

  return (
    <Layout
      kicker={kicker}
      title={title}
    >
      {/* Filter chips */}
      <div className="filter-chips">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            className={`chip${filter === f.value ? ' active' : ''}`}
            onClick={() => setFilter(f.value)}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID de pedido</th>
              <th>Fecha</th>
              <th>Ítems</th>
              <th>Total</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              Array.from({ length: 6 }).map((_, i) => <OrderRowSkeleton key={i} />)
            ) : orders.length === 0 ? (
              <tr>
                <td colSpan={5}>
                  <div className="empty-state" style={{ margin: '8px 0' }}>
                    <p className="empty-state-title">Nada por aquí</p>
                    <p className="empty-state-sub">No hay pedidos con este filtro.</p>
                  </div>
                </td>
              </tr>
            ) : (
              orders.map((order, i) => (
                <motion.tr
                  key={order.id}
                  className="clickable"
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.2, delay: Math.min(i, 7) * 0.035 }}
                  onClick={() => navigate(`/orders/${order.id}`)}
                >
                  <td>
                    <span className="mono" style={{ fontSize: 12 }}>
                      {order.id.slice(0, 8)}…
                    </span>
                  </td>
                  <td style={{ color: 'var(--ink2)', fontSize: 13 }}>
                    {formatDate(order.created_at)}
                  </td>
                  <td style={{ color: 'var(--ink2)', fontSize: 13 }}>
                    {order.item_count} ítem{order.item_count !== 1 ? 's' : ''}
                  </td>
                  <td>
                    <span className="mono" style={{ fontWeight: 600 }}>
                      ${parseFloat(order.total_amount).toFixed(2)}
                    </span>
                  </td>
                  <td>
                    <StatusBadge status={order.status} />
                  </td>
                </motion.tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Layout>
  )
}
