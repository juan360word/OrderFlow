import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, ShoppingCart } from 'lucide-react'
import { productsApi, inventoryApi } from '../lib/api'
import { useCart } from '../store/cart'
import { useToast } from '../store/toast'
import { Layout } from '../components/Layout'
import { ProductImage } from '../components/ProductImage'
import { Stepper } from '../components/Stepper'
import { Skeleton } from '../components/Skeleton'

export function ProductDetailScreen() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { addItem } = useCart()
  const { addToast } = useToast()
  const [qty, setQty] = useState(1)

  const { data: product, isLoading: loadingProduct } = useQuery({
    queryKey: ['product', id],
    queryFn: () => productsApi.get(id!),
    enabled: !!id,
  })

  const { data: stock, isLoading: loadingStock } = useQuery({
    queryKey: ['stock', id],
    queryFn: () => inventoryApi.get(id!),
    enabled: !!id,
    staleTime: 30_000,
  })

  const available = stock?.quantity_available ?? 0
  const hasStock = available > 0

  function handleAdd() {
    if (!product) return
    addItem(product, qty)
    addToast(`${product.name} (×${qty}) añadido al pedido`, 'ok')
  }

  const isLoading = loadingProduct || loadingStock

  return (
    <Layout kicker="Tienda" title={product?.name ?? 'Detalle de producto'}>
      <button
        type="button"
        className="btn btn-ghost"
        onClick={() => navigate('/catalog')}
        style={{ marginBottom: 20 }}
      >
        <ArrowLeft size={15} />
        Volver al catálogo
      </button>

      {isLoading ? (
        <div className="two-col">
          <div className="img-placeholder" style={{ height: 320 }}>
            <Skeleton width="60%" height={14} />
          </div>
          <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <Skeleton height={28} width="70%" />
            <Skeleton height={14} width="40%" />
            <Skeleton height={14} />
            <Skeleton height={14} width="80%" />
            <Skeleton height={56} radius={14} />
            <Skeleton height={40} radius={14} />
          </div>
        </div>
      ) : !product ? (
        <div className="empty-state">
          <p className="empty-state-title">Producto no encontrado</p>
        </div>
      ) : (
        <div className="two-col">
          <ProductImage
            url={product.image_url}
            alt={product.name}
            placeholder={`foto · ${product.name.toLowerCase()}`}
            className="img-placeholder"
            style={{ height: 320 }}
            rounded={18}
          />

          {/* Info card */}
          <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <button
              type="button"
              className="btn btn-ghost"
              style={{ alignSelf: 'flex-start', padding: '4px 0' }}
              onClick={() => navigate('/catalog')}
            >
              <ArrowLeft size={14} />
              Volver
            </button>

            <h2 style={{ fontSize: 22, fontWeight: 800, letterSpacing: '-0.02em', color: 'var(--ink)' }}>
              {product.name}
            </h2>

            <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 12, color: 'var(--ink2)' }}>
              {product.sku} · {product.currency}
            </span>

            {product.description && (
              <p style={{ fontSize: 14, color: 'var(--ink2)', lineHeight: 1.6 }}>
                {product.description}
              </p>
            )}

            {/* Price + Stock block */}
            <div
              style={{
                background: 'var(--sunk)',
                boxShadow: 'var(--sh-in)',
                borderRadius: 14,
                padding: '16px 18px',
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 16,
              }}
            >
              <div>
                <p className="section-label" style={{ marginBottom: 4 }}>Precio</p>
                <p
                  className="mono"
                  style={{ fontSize: 24, fontWeight: 600, color: 'var(--ink)' }}
                >
                  ${parseFloat(product.price).toFixed(2)}
                </p>
              </div>
              <div>
                <p className="section-label" style={{ marginBottom: 4 }}>Stock</p>
                {loadingStock ? (
                  <Skeleton height={20} />
                ) : (
                  <p
                    style={{
                      fontSize: 15,
                      fontWeight: 700,
                      color: available <= 0 ? 'var(--bad)' : available <= 5 ? 'var(--warn)' : 'var(--ok)',
                    }}
                  >
                    {available <= 0
                      ? 'Sin stock'
                      : available <= 5
                        ? `Últimas ${available} u.`
                        : `${available} disponibles`}
                  </p>
                )}
              </div>
            </div>

            {/* Stepper + Add */}
            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <Stepper
                value={qty}
                min={1}
                max={Math.max(1, available)}
                onChange={setQty}
              />
              <button
                type="button"
                className="btn btn-primary"
                style={{ flex: 1 }}
                onClick={handleAdd}
                disabled={!hasStock}
              >
                <ShoppingCart size={15} />
                {hasStock ? 'Añadir al pedido' : 'Sin stock'}
              </button>
            </div>
          </div>
        </div>
      )}
    </Layout>
  )
}
