import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, Check } from 'lucide-react'
import { ordersApi } from '../lib/api'
import { useToast } from '../store/toast'
import { useAuth } from '../store/auth'
import { Layout } from '../components/Layout'
import { StatusBadge } from '../components/StatusBadge'
import { Skeleton } from '../components/Skeleton'
import { ShippingCard } from '../components/ShippingCard'
import { ApiError } from '../lib/api'

function formatDate(iso: string) {
  return new Date(iso).toLocaleString('es-MX', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function OrderDetailScreen() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { addToast } = useToast()
  const { user } = useAuth()
  const qc = useQueryClient()
  const isAdmin = user?.role === 'admin'

  const { data: order, isLoading } = useQuery({
    queryKey: ['order', id],
    queryFn: () => ordersApi.get(id!),
    enabled: !!id,
  })

  const cancelMut = useMutation({
    mutationFn: () => ordersApi.cancel(id!, undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['order', id] })
      qc.invalidateQueries({ queryKey: ['orders'] })
      addToast('Pedido cancelado', 'warn')
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        addToast(e.detail ?? 'No se pudo cancelar.', 'bad')
      }
    },
  })

  // Confirmar es lo que convierte la reserva de stock en una venta firme, y
  // por eso es exclusivo del admin: el backend rechaza a cualquier otro.
  const confirmMut = useMutation({
    mutationFn: () => ordersApi.confirm(id!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['order', id] })
      qc.invalidateQueries({ queryKey: ['orders'] })
      addToast('Pedido confirmado', 'ok')
    },
    onError: (e) => {
      if (e instanceof ApiError) addToast(e.detail, 'bad')
      else addToast('No se pudo confirmar.', 'bad')
    },
  })

  // La máquina de estados del backend solo permite PENDING → CONFIRMED y
  // PENDING → CANCELLED. La interfaz refleja esas mismas reglas en vez de
  // ofrecer botones que el servidor va a rechazar.
  const canCancel = order?.status === 'pending'
  const canConfirm = isAdmin && order?.status === 'pending'

  return (
    <Layout kicker="Pedido" title="Detalle de pedido">
      <button
        type="button"
        className="btn btn-ghost"
        onClick={() => navigate('/orders')}
        style={{ marginBottom: 20 }}
      >
        <ArrowLeft size={15} />
        Volver a pedidos
      </button>

      {isLoading ? (
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Skeleton height={24} width="50%" />
          <Skeleton height={14} width="30%" />
          <Skeleton height={120} radius={14} />
        </div>
      ) : !order ? (
        <div className="empty-state">
          <p className="empty-state-title">Pedido no encontrado</p>
        </div>
      ) : (
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* Header */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
            <div>
              <p
                className="mono"
                style={{ fontSize: 22, fontWeight: 600, color: 'var(--ink)', letterSpacing: '-0.02em' }}
              >
                #{order.id.slice(0, 8).toUpperCase()}
              </p>
              <p style={{ fontSize: 13, color: 'var(--ink2)', marginTop: 4 }}>
                Creado el {formatDate(order.created_at)}
              </p>
            </div>
            <StatusBadge status={order.status} />
          </div>

          {/* Items table */}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Producto</th>
                  <th>Cant.</th>
                  <th>Precio</th>
                  <th>Subtotal</th>
                </tr>
              </thead>
              <tbody>
                {order.items.map((item) => (
                  <tr key={item.product_id}>
                    <td>
                      <div>
                        <p style={{ fontWeight: 600, fontSize: 14 }}>{item.product_name}</p>
                        <p className="mono" style={{ fontSize: 11, color: 'var(--ink2)' }}>
                          {item.product_sku}
                        </p>
                      </div>
                    </td>
                    <td className="mono">{item.quantity}</td>
                    <td className="mono">${parseFloat(item.unit_price).toFixed(2)}</td>
                    <td className="mono" style={{ fontWeight: 600 }}>
                      ${parseFloat(item.subtotal).toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* A dónde va. Para el admin es lo que tiene que hacer con el
              pedido, así que va resaltado y encima de los botones. */}
          <ShippingCard address={order.shipping_address} emphasis={isAdmin} />

          {/* Footer */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: 16,
              borderTop: '1px solid var(--line)',
              paddingTop: 16,
            }}
          >
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {canConfirm && (
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => confirmMut.mutate()}
                  disabled={confirmMut.isPending || cancelMut.isPending}
                >
                  <Check size={15} />
                  {confirmMut.isPending ? 'Confirmando…' : 'Aceptar pedido'}
                </button>
              )}

              {canCancel ? (
                <button
                  type="button"
                  className="btn btn-danger"
                  onClick={() => cancelMut.mutate()}
                  disabled={cancelMut.isPending || confirmMut.isPending}
                >
                  {cancelMut.isPending ? 'Cancelando…' : 'Cancelar pedido'}
                </button>
              ) : (
                <button
                  type="button"
                  className="btn"
                  disabled
                  style={{ color: 'var(--ink2)' }}
                  aria-label="Sin acciones disponibles"
                >
                  {order.status === 'confirmed' ? 'Ya confirmado' : 'Ya cancelado'}
                </button>
              )}
            </div>

            <div style={{ textAlign: 'right' }}>
              <p className="section-label">Total</p>
              <p
                className="mono"
                style={{ fontSize: 24, fontWeight: 600, color: 'var(--ink)' }}
              >
                ${parseFloat(order.total_amount).toFixed(2)}{' '}
                <span style={{ fontSize: 12, color: 'var(--ink2)' }}>{order.currency}</span>
              </p>
            </div>
          </div>

          {/* Cancellation note */}
          {order.cancellation_reason && (
            <div
              style={{
                background: 'var(--sunk)',
                boxShadow: 'var(--sh-in)',
                borderRadius: 12,
                padding: '12px 14px',
                fontSize: 13,
                color: 'var(--ink2)',
              }}
            >
              <span style={{ fontWeight: 700, color: 'var(--bad)' }}>Motivo: </span>
              {order.cancellation_reason}
            </div>
          )}
        </div>
      )}
    </Layout>
  )
}
