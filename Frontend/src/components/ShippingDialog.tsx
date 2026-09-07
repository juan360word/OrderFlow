import { useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useForm, type SubmitHandler } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Truck, X } from 'lucide-react'
import type { ShippingAddressInput } from '../lib/api'
import { COUNTRIES } from '../lib/countries'

/** Espejo de `ShippingAddressInput` del backend, con los mismos límites. */
const schema = z.object({
  recipient_name: z.string().trim().min(2, 'Escribe el nombre de quien recibe').max(200),
  phone: z
    .string()
    .trim()
    .min(7, 'Teléfono demasiado corto')
    .max(32)
    .regex(/^[0-9+()\-.\s]+$/, 'Solo números, espacios y + - ( )'),
  line1: z.string().trim().min(3, 'Escribe la dirección').max(255),
  line2: z.string().trim().max(255).optional(),
  city: z.string().trim().min(1, 'Requerido').max(120),
  region: z.string().trim().min(1, 'Requerido').max(120),
  postal_code: z.string().trim().max(20).optional(),
  country: z.string().length(2),
  notes: z.string().trim().max(500).optional(),
})

type Form = z.infer<typeof schema>

/**
 * Dónde hay que entregar el pedido.
 *
 * Se guarda la última dirección usada para no obligar a reescribirla en cada
 * compra. Vive solo en este navegador — el backend nunca la asocia a la
 * cuenta, porque la dirección pertenece al pedido, no a la persona.
 */
const STORAGE_KEY = 'of_last_shipping'

const EMPTY: Form = {
  recipient_name: '',
  phone: '',
  line1: '',
  line2: '',
  city: '',
  region: '',
  postal_code: '',
  country: 'CO',
  notes: '',
}

function loadLastUsed(): Form {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return EMPTY
    // Se valida antes de usarla: lo guardado puede ser de una versión anterior
    // del formulario, y un dato corrupto no debe romper la pantalla.
    const parsed = schema.partial().safeParse(JSON.parse(raw))
    return parsed.success ? { ...EMPTY, ...parsed.data } : EMPTY
  } catch {
    return EMPTY
  }
}

function rememberForNextTime(values: Form) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(values))
  } catch {
    // Modo privado o almacenamiento lleno: recordar la dirección es una
    // comodidad, nunca un motivo para no dejar comprar.
  }
}

type Props = {
  open: boolean
  itemCount: number
  total: number
  pending: boolean
  onCancel: () => void
  onSubmit: (address: ShippingAddressInput) => void
}

export function ShippingDialog({
  open,
  itemCount,
  total,
  pending,
  onCancel,
  onSubmit,
}: Props) {
  const form = useForm<Form>({ resolver: zodResolver(schema), defaultValues: EMPTY })
  const { reset } = form

  // Al abrirse, y no al montarse: el diálogo vive en el árbol todo el tiempo,
  // así que leer la dirección guardada una sola vez dejaría el formulario
  // desincronizado después de la primera compra.
  useEffect(() => {
    if (open) reset(loadLastUsed())
  }, [open, reset])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !pending) onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, pending, onCancel])

  const submit: SubmitHandler<Form> = (values) => {
    rememberForNextTime(values)
    // Los opcionales en blanco se omiten en lugar de viajar como "".
    const trimmed = (value?: string) => (value && value.length > 0 ? value : undefined)
    onSubmit({
      recipient_name: values.recipient_name,
      phone: values.phone,
      line1: values.line1,
      line2: trimmed(values.line2),
      city: values.city,
      region: values.region,
      postal_code: trimmed(values.postal_code),
      country: values.country,
      notes: trimmed(values.notes),
    })
  }

  const err = form.formState.errors

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          role="dialog"
          aria-modal="true"
          aria-label="Datos de envío"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          onClick={() => !pending && onCancel()}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 200,
            display: 'grid',
            placeItems: 'center',
            padding: 20,
            background: 'var(--scrim)',
            backdropFilter: 'blur(6px)',
            overflowY: 'auto',
          }}
        >
          <motion.div
            onClick={(e) => e.stopPropagation()}
            initial={{ scale: 0.94, y: 24, opacity: 0 }}
            animate={{ scale: 1, y: 0, opacity: 1 }}
            exit={{ scale: 0.96, y: 12, opacity: 0 }}
            transition={{ type: 'spring', stiffness: 300, damping: 26 }}
            className="card"
            style={{ maxWidth: 520, width: '100%', padding: 28, margin: 'auto' }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                justifyContent: 'space-between',
                gap: 12,
              }}
            >
              <div style={{ display: 'flex', gap: 13, alignItems: 'center' }}>
                <span
                  style={{
                    width: 42,
                    height: 42,
                    borderRadius: 13,
                    background: 'var(--sunk)',
                    boxShadow: 'var(--sh-in)',
                    display: 'grid',
                    placeItems: 'center',
                    color: 'var(--accent)',
                    flexShrink: 0,
                  }}
                >
                  <Truck size={20} />
                </span>
                <div>
                  <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--ink)' }}>
                    ¿A dónde lo enviamos?
                  </h2>
                  <p style={{ fontSize: 12.5, color: 'var(--ink2)', marginTop: 3 }}>
                    {itemCount} {itemCount === 1 ? 'ítem' : 'ítems'} · ${total.toFixed(2)}
                  </p>
                </div>
              </div>
              <button
                type="button"
                className="btn btn-ghost btn-icon"
                onClick={onCancel}
                disabled={pending}
                aria-label="Cerrar sin comprar"
              >
                <X size={16} />
              </button>
            </div>

            <form
              onSubmit={form.handleSubmit(submit)}
              style={{ display: 'flex', flexDirection: 'column', gap: 13, marginTop: 22 }}
              noValidate
            >
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div className="input-group">
                  <label className="input-label" htmlFor="sh-name">Quién recibe</label>
                  <input
                    id="sh-name"
                    className="input"
                    autoComplete="name"
                    {...form.register('recipient_name')}
                  />
                  <span className="input-error-msg">{err.recipient_name?.message}</span>
                </div>
                <div className="input-group">
                  <label className="input-label" htmlFor="sh-phone">Teléfono</label>
                  <input
                    id="sh-phone"
                    className="input"
                    inputMode="tel"
                    autoComplete="tel"
                    placeholder="+57 300 123 4567"
                    {...form.register('phone')}
                  />
                  <span className="input-error-msg">{err.phone?.message}</span>
                </div>
              </div>

              <div className="input-group">
                <label className="input-label" htmlFor="sh-line1">Dirección</label>
                <input
                  id="sh-line1"
                  className="input"
                  autoComplete="address-line1"
                  placeholder="Calle 123 #45-67"
                  {...form.register('line1')}
                />
                <span className="input-error-msg">{err.line1?.message}</span>
              </div>

              <div className="input-group">
                <label className="input-label" htmlFor="sh-line2">
                  Apartamento, torre o referencia <span style={{ opacity: 0.6 }}>(opcional)</span>
                </label>
                <input
                  id="sh-line2"
                  className="input"
                  autoComplete="address-line2"
                  {...form.register('line2')}
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div className="input-group">
                  <label className="input-label" htmlFor="sh-city">Ciudad</label>
                  <input
                    id="sh-city"
                    className="input"
                    autoComplete="address-level2"
                    {...form.register('city')}
                  />
                  <span className="input-error-msg">{err.city?.message}</span>
                </div>
                <div className="input-group">
                  <label className="input-label" htmlFor="sh-region">Departamento</label>
                  <input
                    id="sh-region"
                    className="input"
                    autoComplete="address-level1"
                    {...form.register('region')}
                  />
                  <span className="input-error-msg">{err.region?.message}</span>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div className="input-group">
                  <label className="input-label" htmlFor="sh-postal">
                    Código postal <span style={{ opacity: 0.6 }}>(opcional)</span>
                  </label>
                  <input
                    id="sh-postal"
                    className="input"
                    autoComplete="postal-code"
                    {...form.register('postal_code')}
                  />
                </div>
                <div className="input-group">
                  <label className="input-label" htmlFor="sh-country">País</label>
                  <select
                    id="sh-country"
                    className="input"
                    autoComplete="country"
                    {...form.register('country')}
                  >
                    {COUNTRIES.map((c) => (
                      <option key={c.code} value={c.code}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="input-group">
                <label className="input-label" htmlFor="sh-notes">
                  Instrucciones de entrega <span style={{ opacity: 0.6 }}>(opcional)</span>
                </label>
                <textarea
                  id="sh-notes"
                  className="input"
                  rows={2}
                  style={{ resize: 'vertical' }}
                  placeholder="Dejar en portería, timbre dañado…"
                  {...form.register('notes')}
                />
              </div>

              <div style={{ display: 'flex', gap: 10, marginTop: 6 }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={onCancel}
                  disabled={pending}
                >
                  Volver
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  style={{ flex: 1 }}
                  disabled={pending}
                >
                  {pending ? 'Enviando pedido…' : 'Confirmar pedido'}
                </button>
              </div>
            </form>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
