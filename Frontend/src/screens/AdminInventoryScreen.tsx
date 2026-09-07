import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm, type SubmitHandler } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { productsApi, inventoryApi } from '../lib/api'
import { useToast } from '../store/toast'
import { Layout } from '../components/Layout'
import { Skeleton } from '../components/Skeleton'
import { ApiError } from '../lib/api'
import type { ProductResponse } from '../lib/schemas'

// Local schema: plain number (no coerce) — valueAsNumber on input handles conversion
const adjustSchema = z.object({
  delta: z.number().int().min(-1_000_000).max(1_000_000),
  reason: z.string().min(3, 'Mínimo 3 caracteres').max(255),
})
type AdjustForm = z.infer<typeof adjustSchema>

type RowData = {
  product: ProductResponse
  available: number
  reserved: number
}

function InventoryRow({
  row,
  onAdjust,
  onQuickAdjust,
  busy,
}: {
  row: RowData
  onAdjust: (product: ProductResponse) => void
  onQuickAdjust: (product: ProductResponse, delta: number) => void
  busy: boolean
}) {
  const available = row.available
  const statusColor =
    available <= 0 ? 'var(--bad)' : available <= 5 ? 'var(--warn)' : 'var(--ok)'
  const statusLabel =
    available <= 0 ? 'Agotado' : available <= 10 ? 'Stock bajo' : 'En stock'

  return (
    <tr>
      <td className="mono" style={{ fontSize: 12 }}>
        {row.product.sku}
      </td>
      <td style={{ fontWeight: 600, fontSize: 14 }}>{row.product.name}</td>
      <td>
        <span className="badge" style={{ color: statusColor }}>
          {statusLabel}
        </span>
      </td>
      <td>
        <span className="mono" style={{ fontWeight: 600 }}>
          {available}
        </span>
        {row.reserved > 0 && (
          <span style={{ fontSize: 11, color: 'var(--ink2)', marginLeft: 6 }}>
            ({row.reserved} res.)
          </span>
        )}
      </td>
      <td style={{ whiteSpace: 'nowrap' }}>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => onQuickAdjust(row.product, -1)}
          disabled={available <= 0 || busy}
          title="Retirar una unidad"
        >
          −1
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => onQuickAdjust(row.product, 1)}
          disabled={busy}
          title="Añadir una unidad"
        >
          +1
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => onAdjust(row.product)}
        >
          Ajustar
        </button>
      </td>
    </tr>
  )
}

export function AdminInventoryScreen() {
  const qc = useQueryClient()
  const { addToast } = useToast()
  const [selected, setSelected] = useState<ProductResponse | null>(null)

  // Clave propia: esta lista pide otros filtros que la de Productos. Con la
  // misma clave, React Query servía a las dos la respuesta de la que hubiera
  // cargado primero, y una de ellas mostraba datos que no había pedido.
  const { data: productsData, isLoading } = useQuery({
    queryKey: ['products', 'inventory', 'active-only'],
    queryFn: () => productsApi.listAll(),
  })
  const products = productsData?.items ?? []

  const stockQueries = useQuery({
    queryKey: ['all-stock', products.map((p) => p.id).join(',')],
    queryFn: async () => {
      const results = await Promise.allSettled(
        products.map((p) => inventoryApi.get(p.id)),
      )
      return results.map((r, i) => ({
        product: products[i],
        available: r.status === 'fulfilled' ? r.value.quantity_available : 0,
        reserved: r.status === 'fulfilled' ? r.value.quantity_reserved : 0,
      }))
    },
    enabled: products.length > 0,
    staleTime: 30_000,
  })

  const rows: RowData[] = stockQueries.data ?? []

  const form = useForm<AdjustForm>({
    resolver: zodResolver(adjustSchema),
    defaultValues: { delta: 0, reason: '' },
  })

  const adjustMut = useMutation({
    mutationFn: (vals: AdjustForm) =>
      inventoryApi.adjust(selected!.id, { delta: vals.delta, reason: vals.reason }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['all-stock'] })
      qc.invalidateQueries({ queryKey: ['stock', selected?.id] })
      qc.invalidateQueries({ queryKey: ['products'] })
      form.reset({ delta: 0, reason: '' })
      setSelected(null)
      addToast('Stock ajustado', 'ok')
    },
    onError: (e) => {
      if (e instanceof ApiError) addToast(e.detail, 'bad')
    },
  })

  const onSubmit: SubmitHandler<AdjustForm> = (v) => adjustMut.mutate(v)

  const quickMut = useMutation({
    mutationFn: ({ product, delta }: { product: ProductResponse; delta: number }) =>
      inventoryApi.adjust(product.id, {
        delta,
        // El motivo no es decorativo: queda en el histórico de movimientos, y
        // un ajuste sin explicación es stock que cambió sin que nadie sepa por qué.
        reason: delta > 0 ? 'Ajuste rápido: +1' : 'Ajuste rápido: -1',
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['all-stock'] })
    },
    onError: (e) => {
      if (e instanceof ApiError) addToast(e.detail, 'bad')
    },
  })

  function quickAdjust(product: ProductResponse, delta: number) {
    quickMut.mutate({ product, delta })
  }

  function openAdjust(p: ProductResponse) {
    setSelected(p)
    form.reset({ delta: 0, reason: '' })
  }

  return (
    <Layout kicker="Administración" title="Inventario">
      <div className="two-col" style={{ gridTemplateColumns: '1fr 320px' }}>
        {/* Table */}
        <div>
          {isLoading || stockQueries.isLoading ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} height={44} />
              ))}
            </div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>SKU</th>
                    <th>Producto</th>
                    <th>Estado</th>
                    <th>Stock</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <InventoryRow
                      key={row.product.id}
                      row={row}
                      onAdjust={openAdjust}
                      onQuickAdjust={quickAdjust}
                      busy={adjustMut.isPending || quickMut.isPending}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Adjust panel */}
        {selected && (
          <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div>
              <h3 style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink)' }}>
                Ajustar stock
              </h3>
              <p className="mono" style={{ fontSize: 12, color: 'var(--ink2)', marginTop: 4 }}>
                {selected.sku} · {selected.name}
              </p>
            </div>

            <form
              onSubmit={form.handleSubmit(onSubmit)}
              style={{ display: 'flex', flexDirection: 'column', gap: 14 }}
              noValidate
            >
              <div className="input-group">
                <label className="input-label" htmlFor="delta">
                  Delta (+ añadir, − retirar)
                </label>
                <input
                  id="delta"
                  type="number"
                  className="input mono"
                  {...form.register('delta', { valueAsNumber: true })}
                />
                <span className="input-error-msg">
                  {form.formState.errors.delta?.message}
                </span>
              </div>

              <div className="input-group">
                <label className="input-label" htmlFor="reason">
                  Motivo
                </label>
                <input
                  id="reason"
                  type="text"
                  className="input"
                  placeholder="Ej: Recepción de mercancía"
                  {...form.register('reason')}
                />
                <span className="input-error-msg">
                  {form.formState.errors.reason?.message}
                </span>
              </div>

              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  type="submit"
                  className="btn btn-primary"
                  style={{ flex: 1 }}
                  disabled={adjustMut.isPending}
                >
                  {adjustMut.isPending ? 'Guardando…' : 'Aplicar ajuste'}
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setSelected(null)}
                >
                  Cancelar
                </button>
              </div>
            </form>
          </div>
        )}
      </div>
    </Layout>
  )
}
