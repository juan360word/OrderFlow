import { useState, useRef } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Trash2, RefreshCw, ShoppingCart } from 'lucide-react'
import { ordersApi } from '../lib/api'
import { useCart } from '../store/cart'
import { useToast } from '../store/toast'
import { Layout } from '../components/Layout'
import { Stepper } from '../components/Stepper'
import { ApiError } from '../lib/api'

function generateIdemKey() {
  return crypto.randomUUID()
}

export function CartScreen() {
  const { items, removeItem, updateQty, clearCart, total, count } = useCart()
  const { addToast } = useToast()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [idemKey, setIdemKey] = useState(generateIdemKey)
  const submitting = useRef(false)

  const createOrder = useMutation({
    mutationFn: () =>
      ordersApi.create(
        { items: items.map((i) => ({ product_id: i.product.id, quantity: i.quantity })) },
        idemKey,
      ),
    onSuccess: (order) => {
      clearCart()
      setIdemKey(generateIdemKey())
      qc.invalidateQueries({ queryKey: ['orders'] })
      addToast('¡Pedido confirmado!', 'ok')
      navigate(`/orders/${order.id}`)
    },
    onError: (e) => {
      if (e instanceof ApiError) {
        addToast(e.detail ?? 'Error al crear el pedido.', 'bad')
      } else {
        addToast('Error inesperado.', 'bad')
      }
    },
    onSettled: () => {
      submitting.current = false
    },
  })

  function handleConfirm() {
    if (submitting.current || items.length === 0) return
    submitting.current = true
    createOrder.mutate()
  }

  return (
    <Layout kicker="Pedido" title="Mi pedido">
      {items.length === 0 ? (
        <div className="empty-state">
          <ShoppingCart size={36} color="var(--ink2)" />
          <p className="empty-state-title">Tu pedido está vacío</p>
          <p className="empty-state-sub">Añade productos del catálogo para empezar.</p>
          <button
            type="button"
            className="btn btn-primary"
            style={{ marginTop: 8 }}
            onClick={() => navigate('/catalog')}
          >
            Ir al catálogo
          </button>
        </div>
      ) : (
        <div className="two-col">
          {/* Line items */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {items.map((item) => {
              const subtotal = parseFloat(item.product.price) * item.quantity
              return (
                <div key={item.product.id} className="card-sm">
                  <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                    {/* Mini thumb */}
                    <div
                      style={{
                        width: 48,
                        height: 48,
                        borderRadius: 10,
                        background: 'var(--sunk)',
                        backgroundImage:
                          'repeating-linear-gradient(135deg, var(--stripe) 0 6px, transparent 6px 12px)',
                        flexShrink: 0,
                      }}
                    />

                    <div style={{ flex: 1, minWidth: 0 }}>
                      <p
                        style={{
                          fontWeight: 700,
                          fontSize: 14,
                          color: 'var(--ink)',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}
                      >
                        {item.product.name}
                      </p>
                      <p
                        className="mono"
                        style={{ fontSize: 12, color: 'var(--ink2)' }}
                      >
                        ${parseFloat(item.product.price).toFixed(2)} × {item.quantity}
                      </p>
                    </div>

                    <Stepper
                      value={item.quantity}
                      min={1}
                      max={9999}
                      onChange={(q) => updateQty(item.product.id, q)}
                    />

                    <p
                      className="mono"
                      style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', minWidth: 64, textAlign: 'right' }}
                    >
                      ${subtotal.toFixed(2)}
                    </p>

                    <button
                      type="button"
                      className="btn btn-ghost"
                      style={{ color: 'var(--ink2)', padding: '6px' }}
                      onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--bad)')}
                      onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--ink2)')}
                      onClick={() => removeItem(item.product.id)}
                      aria-label={`Quitar ${item.product.name}`}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
              )
            })}
          </div>

          {/* Summary panel */}
          <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <h3 style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink)' }}>Resumen</h3>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div className="row-space" style={{ fontSize: 14, color: 'var(--ink2)' }}>
                <span>Ítems</span>
                <span className="mono">{count}</span>
              </div>
              <div className="row-space" style={{ fontSize: 14, color: 'var(--ink2)' }}>
                <span>Envío</span>
                <span>Incluido</span>
              </div>
              <div className="divider" />
              <div className="row-space">
                <span style={{ fontWeight: 700, color: 'var(--ink)' }}>Total</span>
                <span
                  className="mono"
                  style={{ fontSize: 26, fontWeight: 600, color: 'var(--ink)' }}
                >
                  ${total.toFixed(2)}
                </span>
              </div>
            </div>

            {/* Idempotency key */}
            <div className="idem-block">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span className="idem-label">Idempotency-Key</span>
                <button
                  type="button"
                  className="btn btn-ghost"
                  style={{ padding: '2px 6px' }}
                  onClick={() => setIdemKey(generateIdemKey())}
                  title="Regenerar clave"
                  aria-label="Regenerar Idempotency-Key"
                >
                  <RefreshCw size={12} />
                </button>
              </div>
              <span className="idem-value">{idemKey}</span>
            </div>

            <button
              type="button"
              className="btn btn-primary"
              style={{ width: '100%' }}
              onClick={handleConfirm}
              disabled={createOrder.isPending || items.length === 0}
            >
              {createOrder.isPending ? 'Procesando…' : 'Confirmar pedido'}
            </button>
          </div>
        </div>
      )}
    </Layout>
  )
}
