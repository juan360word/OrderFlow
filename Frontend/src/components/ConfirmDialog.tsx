import { useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { AlertTriangle } from 'lucide-react'

type Props = {
  open: boolean
  title: string
  /** Qué va a pasar exactamente, en una frase. */
  body: React.ReactNode
  confirmLabel: string
  cancelLabel?: string
  /** Qué decir mientras la acción está en vuelo. */
  pendingLabel?: string
  /** Pinta el botón de confirmar en rojo: la acción destruye algo. */
  destructive?: boolean
  pending?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * Confirmación para acciones que no se pueden deshacer.
 *
 * Comparte el velo (`--scrim`) con el resto de modales, así que oscurece igual
 * en claro y en oscuro. Escape cierra, y el foco entra en Cancelar y no en
 * Confirmar: si el diálogo aparece cuando no se esperaba, la tecla Enter no
 * debe borrar nada.
 */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  cancelLabel = 'Cancelar',
  pendingLabel = 'Un momento…',
  destructive = false,
  pending = false,
  onConfirm,
  onCancel,
}: Props) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel])

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          role="dialog"
          aria-modal="true"
          aria-label={title}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          onClick={onCancel}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 210,
            display: 'grid',
            placeItems: 'center',
            padding: 20,
            background: 'var(--scrim)',
            backdropFilter: 'blur(6px)',
          }}
        >
          <motion.div
            onClick={(e) => e.stopPropagation()}
            initial={{ scale: 0.92, y: 18, opacity: 0 }}
            animate={{ scale: 1, y: 0, opacity: 1 }}
            exit={{ scale: 0.95, y: 10, opacity: 0 }}
            transition={{ type: 'spring', stiffness: 320, damping: 26 }}
            className="card"
            style={{ maxWidth: 420, width: '100%', padding: 26 }}
          >
            <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
              <span
                style={{
                  flexShrink: 0,
                  width: 40,
                  height: 40,
                  borderRadius: 12,
                  background: 'var(--sunk)',
                  boxShadow: 'var(--sh-in)',
                  display: 'grid',
                  placeItems: 'center',
                  color: destructive ? 'var(--bad)' : 'var(--warn)',
                }}
              >
                <AlertTriangle size={19} />
              </span>
              <div>
                <h3 style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink)' }}>{title}</h3>
                <div style={{ fontSize: 13, color: 'var(--ink2)', marginTop: 8, lineHeight: 1.55 }}>
                  {body}
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', gap: 10, marginTop: 22 }}>
              <button
                type="button"
                className="btn btn-secondary"
                style={{ flex: 1 }}
                onClick={onCancel}
                disabled={pending}
                autoFocus
              >
                {cancelLabel}
              </button>
              <button
                type="button"
                className="btn btn-primary"
                style={{ flex: 1, ...(destructive ? { background: 'var(--bad)', color: '#fff' } : {}) }}
                onClick={onConfirm}
                disabled={pending}
              >
                {pending ? pendingLabel : confirmLabel}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
