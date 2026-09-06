import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Check } from 'lucide-react'
import { ordersApi, ApiError } from '../lib/api'
import { useAuth } from '../store/auth'
import { useToast } from '../store/toast'
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

  const qc = useQueryClient()
  const { addToast } = useToast()

  const { data, isLoading } = useQuery({
    queryKey: ['orders', filter],
    queryFn: () =>
      ordersApi.list({ status: filter === 'all' ? undefined : filter, limit: 50 }),
  })

  const orders = data?.items ?? []

  // Aceptar desde la lista: el admin revisa muchos pedidos seguidos y entrar a
  // cada detalle solo para pulsar un botón es trabajo que no aporta nada.
  const confirmMut = useMutation({
    mutationFn: (orderId: string) => ordersApi.confirm(orderId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['orders'] })
      addToast('Pedido confirmado', 'ok')
    },
    onError: (e) => {
      if (e instanceof ApiError) addToast(e.detail, 'bad')
      else addToast('No se pudo confirmar.', 'bad')
    },
  })

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
              {isAdmin && <th></th>}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              Array.from({ length: 6 }).map((_, i) => <OrderRowSkeleton key={i} />)
            ) : orders.length === 0 ? (
              <tr>
                <td colSpan={isAdmin ? 6 : 5}>
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
                  {isAdmin && (
                    <td style={{ whiteSpace: 'nowrap' }}>
                      {order.status === 'pending' && (
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          style={{ color: 'var(--ok)' }}
                          disabled={confirmMut.isPending}
                          onClick={(event) => {
                            // La fila entera navega al detalle; sin esto, pulsar
                            // el botón confirmaría Y cambiaría de pantalla.
                            event.stopPropagation()
                            confirmMut.mutate(order.id)
                          }}
                        >
                          <Check size={14} />
                          Aceptar
                        </button>
                      )}
                    </td>
                  )}
                </motion.tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Layout>
  )
}
