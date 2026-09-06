import { AnimatePresence, motion } from 'framer-motion'
import { CheckCircle, XCircle, AlertTriangle } from 'lucide-react'
import { useToast } from '../store/toast'

export function ToastStack() {
  const { toasts } = useToast()

  return (
    <div className="toast-stack">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            className={`toast toast-${t.type}`}
            initial={{ opacity: 0, y: 20, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.95 }}
            transition={{ duration: 0.18 }}
          >
            {t.type === 'ok' && <CheckCircle size={16} color="var(--ok)" />}
            {t.type === 'bad' && <XCircle size={16} color="var(--bad)" />}
            {t.type === 'warn' && <AlertTriangle size={16} color="var(--warn)" />}
            <span>{t.msg}</span>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}
