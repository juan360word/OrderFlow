import { motion, AnimatePresence } from 'framer-motion'
import { Check } from 'lucide-react'

type Props = {
  open: boolean
  orderId: string | null
  total: number
  itemCount: number
  onClose: () => void
  onSeeOrder: () => void
}

/** Doce partículas repartidas en círculo alrededor del sello. */
const SPARKS = Array.from({ length: 12 }, (_, i) => {
  const angle = (i / 12) * Math.PI * 2
  return { x: Math.cos(angle) * 130, y: Math.sin(angle) * 130, delay: 0.18 + i * 0.012 }
})

/**
 * El momento de "ya está, compraste".
 *
 * Se muestra al confirmar el pedido, cuando el trabajo difícil ya ocurrió: el
 * stock quedó reservado, el pedido y su evento se guardaron en la misma
 * transacción. La animación existe para que ese instante se sienta cerrado, y
 * el número de pedido está a la vista porque es lo único que el cliente
 * necesita si algo va mal después.
 */
export function PurchaseSuccess({
  open,
  orderId,
  total,
  itemCount,
  onClose,
  onSeeOrder,
}: Props) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          role="dialog"
          aria-modal="true"
          aria-label="Compra confirmada"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          onClick={onClose}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 100,
            display: 'grid',
            placeItems: 'center',
            padding: 20,
            background: 'color-mix(in srgb, var(--ink) 55%, transparent)',
            backdropFilter: 'blur(6px)',
          }}
        >
          <motion.div
            onClick={(e) => e.stopPropagation()}
            initial={{ scale: 0.8, y: 40, opacity: 0 }}
            animate={{ scale: 1, y: 0, opacity: 1 }}
            exit={{ scale: 0.9, y: 20, opacity: 0 }}
            transition={{ type: 'spring', stiffness: 260, damping: 22 }}
            className="card"
            style={{
              maxWidth: 460,
              width: '100%',
              textAlign: 'center',
              padding: '48px 32px 32px',
              position: 'relative',
              overflow: 'hidden',
            }}
          >
            {/* Sello + destellos */}
            <div
              style={{
                position: 'relative',
                height: 128,
                display: 'grid',
                placeItems: 'center',
                marginBottom: 8,
              }}
            >
              {SPARKS.map((spark, i) => (
                <motion.span
                  key={i}
                  initial={{ opacity: 0, x: 0, y: 0, scale: 0 }}
                  animate={{
                    opacity: [0, 1, 0],
                    x: spark.x,
                    y: spark.y,
                    scale: [0, 1.1, 0.2],
                  }}
                  transition={{ duration: 0.85, delay: spark.delay, ease: 'easeOut' }}
                  style={{
                    position: 'absolute',
                    width: 9,
                    height: 9,
                    borderRadius: 3,
                    background: i % 3 === 0 ? 'var(--ok)' : i % 3 === 1 ? 'var(--warn)' : 'var(--accent, var(--ok))',
                  }}
                />
              ))}

              {/* Onda que se expande */}
              <motion.span
                initial={{ scale: 0.4, opacity: 0.55 }}
                animate={{ scale: 2.4, opacity: 0 }}
                transition={{ duration: 1, delay: 0.1, ease: 'easeOut' }}
                style={{
                  position: 'absolute',
                  width: 104,
                  height: 104,
                  borderRadius: '50%',
                  border: '2px solid var(--ok)',
                }}
              />

              <motion.div
                initial={{ scale: 0, rotate: -35 }}
                animate={{ scale: 1, rotate: 0 }}
                transition={{ type: 'spring', stiffness: 300, damping: 14, delay: 0.08 }}
                style={{
                  width: 104,
                  height: 104,
                  borderRadius: '50%',
                  background: 'var(--ok)',
                  display: 'grid',
                  placeItems: 'center',
                  boxShadow: 'var(--sh-btn)',
                }}
              >
                <motion.span
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ type: 'spring', stiffness: 400, damping: 12, delay: 0.3 }}
                  style={{ display: 'grid', placeItems: 'center', color: '#fff' }}
                >
                  <Check size={54} strokeWidth={3.2} />
                </motion.span>
              </motion.div>
            </div>

            <motion.h2
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.34, duration: 0.3 }}
              style={{
                fontSize: 30,
                fontWeight: 800,
                color: 'var(--ink)',
                letterSpacing: '-0.03em',
                lineHeight: 1.15,
              }}
            >
              ¡Gracias por tu compra!
            </motion.h2>

            <motion.p
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.42, duration: 0.3 }}
              style={{ color: 'var(--ink2)', fontSize: 14, marginTop: 10 }}
            >
              Tu pedido de {itemCount} {itemCount === 1 ? 'ítem' : 'ítems'} quedó
              registrado. Te avisaremos cuando lo confirmemos.
            </motion.p>

            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.5, duration: 0.3 }}
              style={{
                marginTop: 22,
                padding: '14px 16px',
                background: 'var(--sunk)',
                boxShadow: 'var(--sh-in)',
                borderRadius: 14,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: 12,
              }}
            >
              <div style={{ textAlign: 'left' }}>
                <p className="section-label">Pedido</p>
                <p className="mono" style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)' }}>
                  #{orderId ? orderId.slice(0, 8).toUpperCase() : '—'}
                </p>
              </div>
              <div style={{ textAlign: 'right' }}>
                <p className="section-label">Total</p>
                <p className="mono" style={{ fontSize: 20, fontWeight: 700, color: 'var(--ink)' }}>
                  ${total.toFixed(2)}
                </p>
              </div>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.58, duration: 0.3 }}
              style={{ display: 'flex', gap: 10, marginTop: 22 }}
            >
              <button
                type="button"
                className="btn btn-primary"
                style={{ flex: 1 }}
                onClick={onSeeOrder}
                autoFocus
              >
                Ver mi pedido
              </button>
              <button type="button" className="btn btn-secondary" onClick={onClose}>
                Seguir comprando
              </button>
            </motion.div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
