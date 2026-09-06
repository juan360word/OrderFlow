import { useState, useDeferredValue } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Search, ShoppingCart } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { productsApi, inventoryApi } from '../lib/api'
import { useCart } from '../store/cart'
import { useToast } from '../store/toast'
import { Layout } from '../components/Layout'
import { ProductImage } from '../components/ProductImage'
import { ProductCardSkeleton } from '../components/Skeleton'
import type { ProductResponse } from '../lib/schemas'

function StockDot({ available }: { available: number }) {
  const color =
    available <= 0 ? 'var(--bad)' : available <= 5 ? 'var(--warn)' : 'var(--ok)'
  return <span className="stock-dot" style={{ background: color }} />
}

function StockLabel({ available }: { available: number }) {
  if (available <= 0) return <span className="text-bad">Sin stock</span>
  if (available <= 5) return <span className="text-warn">Últimas {available} u.</span>
  return <span className="text-ok">Disponible</span>
}

function ProductCard({ product }: { product: ProductResponse }) {
  const { addItem } = useCart()
  const { addToast } = useToast()
  const navigate = useNavigate()

  const { data: stock } = useQuery({
    queryKey: ['stock', product.id],
    queryFn: () => inventoryApi.get(product.id),
    staleTime: 60_000,
  })

  const available = stock?.quantity_available ?? 0
  const hasStock = available > 0

  function handleAdd() {
    addItem(product, 1)
    addToast(`${product.name} añadido al pedido`, 'ok')
  }

  return (
    <motion.div
      className="product-card"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, ease: 'easeOut' }}
    >
      <button
        type="button"
        onClick={() => navigate(`/products/${product.id}`)}
        aria-label={`Ver detalle de ${product.name}`}
        style={{ border: 'none', padding: 0, cursor: 'pointer', width: '100%', background: 'none' }}
      >
        <ProductImage
          url={product.image_url}
          alt={product.name}
          placeholder={`foto · ${product.sku.toLowerCase()}`}
          className="product-thumb"
          style={{ width: '100%' }}
        />
      </button>

      <div className="product-body">
        <div className="product-name-row">
          <span className="product-name">{product.name}</span>
          <span className="product-price mono">
            ${parseFloat(product.price).toFixed(2)}
          </span>
        </div>

        <div className="product-meta">
          <StockDot available={available} />
          <StockLabel available={available} />
          <span>·</span>
          <span className="product-sku">{product.sku}</span>
        </div>

        <button
          type="button"
          className="btn btn-primary btn-sm"
          style={{ marginTop: 'auto' }}
          onClick={handleAdd}
          disabled={!hasStock}
          aria-label={hasStock ? `Añadir ${product.name} al pedido` : 'Sin stock'}
        >
          <ShoppingCart size={14} />
          Añadir al pedido
        </button>
      </div>
    </motion.div>
  )
}

export function CatalogScreen() {
  const [search, setSearch] = useState('')
  const { count } = useCart()
  const navigate = useNavigate()
  const deferred = useDeferredValue(search)

  const { data, isLoading } = useQuery({
    queryKey: ['products', deferred],
    queryFn: () => productsApi.list({ search: deferred || undefined, limit: 100 }),
  })

  const products = data?.items ?? []

  return (
    <Layout
      kicker="Tienda"
      title="Catálogo"
      topbarRight={
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <div className="searchbar">
            <Search size={15} />
            <input
              type="search"
              placeholder="Buscar por nombre o SKU…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Buscar productos"
            />
          </div>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => navigate('/cart')}
            aria-label={`Mi pedido, ${count} ítems`}
          >
            <ShoppingCart size={16} />
            Mi pedido
            {count > 0 && <span className="nav-badge">{count}</span>}
          </button>
        </div>
      }
    >
      {isLoading ? (
        <div className="product-grid">
          {Array.from({ length: 8 }).map((_, i) => (
            <ProductCardSkeleton key={i} />
          ))}
        </div>
      ) : products.length === 0 ? (
        <div className="empty-state">
          <Search size={32} color="var(--ink2)" />
          <p className="empty-state-title">Sin resultados</p>
          <p className="empty-state-sub">
            Ningún producto coincide con «{search}».
          </p>
        </div>
      ) : (
        <div className="product-grid">
          {products.map((p) => (
            <ProductCard key={p.id} product={p} />
          ))}
        </div>
      )}
    </Layout>
  )
}
