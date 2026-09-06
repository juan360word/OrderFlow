import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm, type SubmitHandler } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Plus, X } from 'lucide-react'
import { z } from 'zod'
import { productsApi } from '../lib/api'
import { useToast } from '../store/toast'
import { Layout } from '../components/Layout'
import { Skeleton } from '../components/Skeleton'
import { ApiError } from '../lib/api'
import type { ProductResponse } from '../lib/schemas'

// Local schemas: plain number fields, use valueAsNumber on inputs
const createSchema = z.object({
  name: z.string().min(1, 'Requerido').max(200),
  sku: z
    .string()
    .min(3, 'Mínimo 3 caracteres')
    .max(64)
    .regex(/^[A-Z0-9][A-Z0-9\-_]{2,63}$/, 'Formato: ABC-01 (mayúsculas)'),
  price: z.number().positive('Debe ser mayor a 0'),
  description: z.string().max(5000).optional(),
  initial_stock: z.number().int().min(0).optional(),
})

const updateSchema = z.object({
  name: z.string().min(1).max(200),
  price: z.number().positive('Debe ser mayor a 0'),
  description: z.string().max(5000).optional(),
})

type CreateForm = z.infer<typeof createSchema>
type UpdateForm = z.infer<typeof updateSchema>

export function AdminProductsScreen() {
  const qc = useQueryClient()
  const { addToast } = useToast()
  const [editing, setEditing] = useState<ProductResponse | null>(null)
  const isEditing = !!editing

  // ─── List ──────────────────────────────────────────────────────────────────
  const { data, isLoading } = useQuery({
    queryKey: ['products-admin'],
    queryFn: () => productsApi.list({ limit: 200, include_inactive: true }),
  })
  const products = data?.items ?? []

  // ─── Create form ───────────────────────────────────────────────────────────
  const createForm = useForm<CreateForm>({
    resolver: zodResolver(createSchema),
    defaultValues: { name: '', sku: '', price: 0, description: '', initial_stock: 0 },
  })

  const createMut = useMutation({
    mutationFn: (vals: CreateForm) =>
      productsApi.create({
        name: vals.name,
        sku: vals.sku.toUpperCase(),
        price: vals.price,
        description: vals.description ?? '',
        initial_stock: vals.initial_stock ?? 0,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['products-admin'] })
      qc.invalidateQueries({ queryKey: ['products'] })
      createForm.reset()
      addToast('Producto creado', 'ok')
    },
    onError: (e) => {
      if (e instanceof ApiError) addToast(e.detail, 'bad')
    },
  })

  const onCreateSubmit: SubmitHandler<CreateForm> = (v) => createMut.mutate(v)

  // ─── Update form ───────────────────────────────────────────────────────────
  const updateForm = useForm<UpdateForm>({
    resolver: zodResolver(updateSchema),
  })

  function startEdit(p: ProductResponse) {
    setEditing(p)
    updateForm.reset({ name: p.name, price: parseFloat(p.price), description: p.description })
  }

  const updateMut = useMutation({
    mutationFn: (vals: UpdateForm) =>
      productsApi.update(editing!.id, {
        name: vals.name,
        price: vals.price,
        description: vals.description,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['products-admin'] })
      qc.invalidateQueries({ queryKey: ['products'] })
      setEditing(null)
      addToast('Producto actualizado', 'ok')
    },
    onError: (e) => {
      if (e instanceof ApiError) addToast(e.detail, 'bad')
    },
  })

  const onUpdateSubmit: SubmitHandler<UpdateForm> = (v) => updateMut.mutate(v)

  return (
    <Layout kicker="Administración" title="Productos">
      <div className="two-col" style={{ gridTemplateColumns: '1fr 340px' }}>
        {/* Table */}
        <div>
          {isLoading ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} height={44} />)}
            </div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>SKU</th>
                    <th>Nombre</th>
                    <th>Precio</th>
                    <th>Estado</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {products.map((p) => (
                    <tr key={p.id}>
                      <td className="mono" style={{ fontSize: 12 }}>{p.sku}</td>
                      <td style={{ fontWeight: 600 }}>{p.name}</td>
                      <td className="mono">${parseFloat(p.price).toFixed(2)}</td>
                      <td>
                        <span
                          className="badge"
                          style={{ color: p.is_active ? 'var(--ok)' : 'var(--ink2)' }}
                        >
                          {p.is_active ? 'Activo' : 'Inactivo'}
                        </span>
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => startEdit(p)}
                        >
                          Editar
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Form panel */}
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink)' }}>
              {isEditing ? `Editar · ${editing.sku}` : 'Nuevo producto'}
            </h3>
            {isEditing && (
              <button
                type="button"
                className="btn btn-ghost btn-icon"
                onClick={() => setEditing(null)}
                aria-label="Cancelar edición"
              >
                <X size={15} />
              </button>
            )}
          </div>

          <p style={{ fontSize: 12, color: 'var(--ink2)' }}>
            Validado en cliente antes de enviar a la API.
          </p>

          {!isEditing ? (
            <form
              onSubmit={createForm.handleSubmit(onCreateSubmit)}
              style={{ display: 'flex', flexDirection: 'column', gap: 14 }}
              noValidate
            >
              <div className="input-group">
                <label className="input-label" htmlFor="cn">Nombre</label>
                <input id="cn" type="text" className="input" {...createForm.register('name')} />
                <span className="input-error-msg">{createForm.formState.errors.name?.message}</span>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div className="input-group">
                  <label className="input-label" htmlFor="csku">SKU</label>
                  <input
                    id="csku"
                    type="text"
                    className="input"
                    placeholder="ABC-01"
                    style={{ fontFamily: 'JetBrains Mono, monospace', textTransform: 'uppercase' }}
                    {...createForm.register('sku')}
                  />
                  <span className="input-error-msg">{createForm.formState.errors.sku?.message}</span>
                </div>
                <div className="input-group">
                  <label className="input-label" htmlFor="cprice">Precio</label>
                  <input
                    id="cprice"
                    type="number"
                    step="0.01"
                    className="input"
                    {...createForm.register('price', { valueAsNumber: true })}
                  />
                  <span className="input-error-msg">{createForm.formState.errors.price?.message}</span>
                </div>
              </div>

              <div className="input-group">
                <label className="input-label" htmlFor="cstock">Stock inicial</label>
                <input
                  id="cstock"
                  type="number"
                  className="input"
                  {...createForm.register('initial_stock', { valueAsNumber: true })}
                />
              </div>

              <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                <button
                  type="submit"
                  className="btn btn-primary"
                  style={{ flex: 1 }}
                  disabled={createMut.isPending}
                >
                  <Plus size={15} />
                  {createMut.isPending ? 'Creando…' : 'Crear producto'}
                </button>
              </div>
            </form>
          ) : (
            <form
              onSubmit={updateForm.handleSubmit(onUpdateSubmit)}
              style={{ display: 'flex', flexDirection: 'column', gap: 14 }}
              noValidate
            >
              <div className="input-group">
                <label className="input-label" htmlFor="un">Nombre</label>
                <input id="un" type="text" className="input" {...updateForm.register('name')} />
                <span className="input-error-msg">{updateForm.formState.errors.name?.message}</span>
              </div>

              <div className="input-group">
                <label className="input-label" htmlFor="uprice">Precio</label>
                <input
                  id="uprice"
                  type="number"
                  step="0.01"
                  className="input"
                  {...updateForm.register('price', { valueAsNumber: true })}
                />
                <span className="input-error-msg">{updateForm.formState.errors.price?.message}</span>
              </div>

              <div className="input-group">
                <label className="input-label" htmlFor="udesc">Descripción</label>
                <textarea
                  id="udesc"
                  className="input"
                  rows={3}
                  style={{ resize: 'vertical' }}
                  {...updateForm.register('description')}
                />
              </div>

              <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                <button
                  type="submit"
                  className="btn btn-primary"
                  style={{ flex: 1 }}
                  disabled={updateMut.isPending}
                >
                  {updateMut.isPending ? 'Guardando…' : 'Guardar cambios'}
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setEditing(null)}
                >
                  Cancelar
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </Layout>
  )
}
