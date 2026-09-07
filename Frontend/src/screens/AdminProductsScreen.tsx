import { useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm, type SubmitHandler } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { AlertCircle, ImagePlus, Package, Plus, Power, Trash2, X } from 'lucide-react'
import { z } from 'zod'
import { productsApi } from '../lib/api'
import { useToast } from '../store/toast'
import { Layout } from '../components/Layout'
import { Skeleton } from '../components/Skeleton'
import { ProductImage } from '../components/ProductImage'
import { ConfirmDialog } from '../components/ConfirmDialog'
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
  // El producto cuyo borrado está esperando confirmación, y si el backend ya
  // dijo que no se puede borrar (se vendió) — entonces el mismo diálogo pasa a
  // ofrecer la retirada, que sí lo quita del catálogo del cliente.
  const [pendingDelete, setPendingDelete] = useState<ProductResponse | null>(null)
  const [deleteBlocked, setDeleteBlocked] = useState(false)

  // ─── List ──────────────────────────────────────────────────────────────────
  // La clave nombra el filtro, no solo la pantalla: React Query cachea por
  // clave, así que dos listas distintas con la misma clave se pisan los datos.
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['products', 'admin', 'include-inactive'],
    queryFn: () => productsApi.listAll({ include_inactive: true }),
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
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ['products'] })
      createForm.reset()
      // Saltar a edición: la foto se sube contra el id del producto, así que
      // no puede adjuntarse hasta que exista. Así queda a un clic.
      setEditing(created)
      addToast('Producto creado · añade una foto', 'ok')
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
      qc.invalidateQueries({ queryKey: ['products'] })
      setEditing(null)
      addToast('Producto actualizado', 'ok')
    },
    onError: (e) => {
      if (e instanceof ApiError) addToast(e.detail, 'bad')
    },
  })

  const onUpdateSubmit: SubmitHandler<UpdateForm> = (v) => updateMut.mutate(v)

  // ─── Activar / desactivar ──────────────────────────────────────────────────
  // Retirar de la venta es un borrado suave: la fila sobrevive porque el
  // historial de pedidos la referencia. Por eso "desactivar" y "reactivar" son
  // el mismo PATCH sobre is_active, y no un DELETE de verdad.
  const toggleActiveMut = useMutation({
    mutationFn: (p: ProductResponse) =>
      productsApi.update(p.id, { is_active: !p.is_active }),
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ['products'] })
      if (editing?.id === updated.id) setEditing(updated)
      setPendingDelete(null)
      setDeleteBlocked(false)
      addToast(updated.is_active ? 'Producto reactivado' : 'Retirado de la venta', 'ok')
    },
    onError: (e) => {
      if (e instanceof ApiError) addToast(e.detail, 'bad')
    },
  })

  // ─── Eliminar ──────────────────────────────────────────────────────────────
  // Borrado de verdad, no retirada. Solo es posible si el producto nunca se
  // vendió, y quien lo decide es la base de datos: order_items lo referencia
  // con ON DELETE RESTRICT, así que preguntarlo antes daría una respuesta que
  // otra compra podría invalidar entre la pregunta y el borrado. Por eso se
  // intenta, y un 409 es la respuesta — ahí el diálogo ofrece retirarlo.
  const deleteMut = useMutation({
    mutationFn: (p: ProductResponse) => productsApi.delete(p.id, { permanent: true }),
    onSuccess: (_result, deleted) => {
      qc.invalidateQueries({ queryKey: ['products'] })
      if (editing?.id === deleted.id) setEditing(null)
      setPendingDelete(null)
      setDeleteBlocked(false)
      addToast('Producto eliminado', 'ok')
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) {
        setDeleteBlocked(true)
        return
      }
      if (e instanceof ApiError) addToast(e.detail, 'bad')
      else addToast('No se pudo eliminar el producto.', 'bad')
      setPendingDelete(null)
    },
  })

  function closeDeleteDialog() {
    setPendingDelete(null)
    setDeleteBlocked(false)
  }

  // ─── Foto ──────────────────────────────────────────────────────────────────
  const fileInputRef = useRef<HTMLInputElement>(null)

  const uploadImageMut = useMutation({
    mutationFn: ({ id, file }: { id: string; file: File }) =>
      productsApi.uploadImage(id, file),
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ['products'] })
      setEditing(updated)
      addToast('Foto actualizada', 'ok')
    },
    onError: (e) => {
      if (e instanceof ApiError) addToast(e.detail, 'bad')
      else addToast('No se pudo subir la imagen.', 'bad')
    },
  })

  const removeImageMut = useMutation({
    mutationFn: (id: string) => productsApi.deleteImage(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['products'] })
      setEditing((current) => (current ? { ...current, image_url: null } : current))
      addToast('Foto eliminada', 'ok')
    },
    onError: (e) => {
      if (e instanceof ApiError) addToast(e.detail, 'bad')
    },
  })

  function onPickFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    // Se limpia el input para que elegir el mismo archivo dos veces seguidas
    // vuelva a disparar el evento: el navegador no emite 'change' si el valor
    // no cambió.
    event.target.value = ''
    if (!file || !editing) return
    uploadImageMut.mutate({ id: editing.id, file })
  }

  return (
    <Layout kicker="Administración" title="Productos">
      <ConfirmDialog
        open={pendingDelete !== null}
        destructive={!deleteBlocked}
        pending={deleteMut.isPending || toggleActiveMut.isPending}
        pendingLabel={deleteBlocked ? 'Retirando…' : 'Eliminando…'}
        title={
          deleteBlocked ? 'Este producto ya se vendió' : `¿Eliminar ${pendingDelete?.name ?? ''}?`
        }
        body={
          deleteBlocked ? (
            <>
              No puede borrarse: aparece en pedidos ya realizados y borrarlo
              destruiría ese historial.{' '}
              {pendingDelete?.is_active
                ? 'Puedes retirarlo de la venta — desaparece del catálogo del cliente igual, y los pedidos antiguos siguen siendo legibles.'
                : 'Ya está retirado de la venta, así que el cliente no lo ve; solo queda visible aquí para que su historial siga teniendo sentido.'}
            </>
          ) : (
            <>
              Se borrará <strong>{pendingDelete?.sku}</strong> junto con su foto y
              su stock, y dejará de verse en el catálogo del cliente. No se puede
              deshacer.
            </>
          )
        }
        confirmLabel={
          deleteBlocked
            ? pendingDelete?.is_active
              ? 'Retirar de la venta'
              : 'Entendido'
            : 'Eliminar'
        }
        cancelLabel={deleteBlocked ? 'Dejarlo como está' : 'Cancelar'}
        onCancel={closeDeleteDialog}
        onConfirm={() => {
          if (!pendingDelete) return
          if (!deleteBlocked) {
            deleteMut.mutate(pendingDelete)
          } else if (pendingDelete.is_active) {
            toggleActiveMut.mutate(pendingDelete)
          } else {
            closeDeleteDialog()
          }
        }}
      />

      <div className="two-col" style={{ gridTemplateColumns: '1fr 340px' }}>
        {/* Table */}
        <div>
          {isLoading ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} height={44} />)}
            </div>
          ) : error ? (
            /* Una tabla vacía y una tabla que no se pudo cargar se parecían
               demasiado: cuando la petición fallaba, la pantalla decía "no hay
               productos" y el fallo quedaba invisible. */
            <div className="empty-state">
              <AlertCircle size={30} color="var(--bad)" />
              <p className="empty-state-title">No se pudo cargar el catálogo</p>
              <p className="empty-state-sub">
                {error instanceof ApiError ? error.detail : 'Error de conexión con la API.'}
              </p>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                style={{ marginTop: 12 }}
                onClick={() => refetch()}
              >
                Reintentar
              </button>
            </div>
          ) : products.length === 0 ? (
            <div className="empty-state">
              <Package size={30} color="var(--ink2)" />
              <p className="empty-state-title">Todavía no hay productos</p>
              <p className="empty-state-sub">Crea el primero con el formulario de la derecha.</p>
            </div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th></th>
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
                      <td style={{ width: 52 }}>
                        <ProductImage
                          url={p.image_url}
                          alt={p.name}
                          placeholder=""
                          style={{
                            width: 40,
                            height: 40,
                            borderRadius: 8,
                            background: 'var(--sunk)',
                            backgroundImage:
                              'repeating-linear-gradient(135deg, var(--stripe) 0 5px, transparent 5px 10px)',
                          }}
                          rounded={8}
                        />
                      </td>
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
                      {/* Tres acciones en una columna estrecha: solo la
                          primera lleva texto. Las otras dos son iconos con
                          `title` y `aria-label`, porque con las tres
                          etiquetadas la fila se pasaba del ancho del panel y
                          la última quedaba fuera de la pantalla. */}
                      <td>
                        <div
                          style={{
                            display: 'flex',
                            gap: 2,
                            justifyContent: 'flex-end',
                            alignItems: 'center',
                          }}
                        >
                          <button
                            type="button"
                            className="btn btn-ghost btn-sm"
                            onClick={() => startEdit(p)}
                          >
                            Editar
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost btn-icon"
                            onClick={() => toggleActiveMut.mutate(p)}
                            disabled={toggleActiveMut.isPending}
                            title={p.is_active ? 'Retirar de la venta' : 'Reactivar'}
                            aria-label={
                              p.is_active ? `Retirar ${p.name} de la venta` : `Reactivar ${p.name}`
                            }
                            style={{ color: p.is_active ? 'var(--ink2)' : 'var(--ok)' }}
                          >
                            <Power size={15} />
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost btn-icon"
                            onClick={() => {
                              setDeleteBlocked(false)
                              setPendingDelete(p)
                            }}
                            disabled={deleteMut.isPending}
                            title="Eliminar el producto"
                            aria-label={`Eliminar ${p.name}`}
                            style={{ color: 'var(--bad)' }}
                          >
                            <Trash2 size={15} />
                          </button>
                        </div>
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
                <label className="input-label" htmlFor="cdesc">Descripción</label>
                <textarea
                  id="cdesc"
                  className="input"
                  rows={3}
                  style={{ resize: 'vertical' }}
                  placeholder="Qué es y para qué sirve"
                  {...createForm.register('description')}
                />
                <span className="input-error-msg">
                  {createForm.formState.errors.description?.message}
                </span>
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
                <span className="input-label">Foto</span>
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploadImageMut.isPending}
                  aria-label="Cambiar la foto del producto"
                  style={{
                    border: '1px dashed var(--line)',
                    borderRadius: 12,
                    padding: 0,
                    background: 'none',
                    cursor: uploadImageMut.isPending ? 'wait' : 'pointer',
                    position: 'relative',
                    overflow: 'hidden',
                  }}
                >
                  <ProductImage
                    url={editing.image_url}
                    alt={editing.name}
                    placeholder={uploadImageMut.isPending ? 'subiendo…' : 'clic para subir foto'}
                    style={{
                      height: 150,
                      width: '100%',
                      background: 'var(--sunk)',
                      backgroundImage:
                        'repeating-linear-gradient(135deg, var(--stripe) 0 8px, transparent 8px 16px)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                    rounded={11}
                  />
                </button>

                {/* Oculto: el botón de arriba es el que se ve. Un input de
                    archivo nativo no se puede estilar de forma fiable entre
                    navegadores, así que el patrón es dispararlo por código. */}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/png,image/jpeg,image/gif,image/webp"
                  hidden
                  onChange={onPickFile}
                />

                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={uploadImageMut.isPending}
                    style={{ flex: 1 }}
                  >
                    <ImagePlus size={14} />
                    {editing.image_url ? 'Cambiar foto' : 'Subir foto'}
                  </button>
                  {editing.image_url && (
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => removeImageMut.mutate(editing.id)}
                      disabled={removeImageMut.isPending}
                      aria-label="Quitar la foto"
                      style={{ color: 'var(--bad)' }}
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
                <span style={{ fontSize: 11, color: 'var(--ink2)', marginTop: 4 }}>
                  JPEG, PNG, GIF o WebP · máximo 2 MB
                </span>
              </div>

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
