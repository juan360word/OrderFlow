import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { Sidebar } from './Sidebar'
import { useSidebar } from '../store/sidebar'
import { ToastStack } from './Toast'

type Props = {
  kicker?: string
  title: string
  topbarRight?: ReactNode
  children: ReactNode
}

const pageVariants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
}

export function Layout({ kicker, title, topbarRight, children }: Props) {
  const { collapsed, toggle } = useSidebar()

  return (
    <div className={`app-shell${collapsed ? ' nav-collapsed' : ''}`}>
      <Sidebar />
      <main className="main-content">
        <header className="topbar">
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 14, minWidth: 0 }}>
            {/* El mismo cuadrado azul, ahora en la barra superior: es lo que
                devuelve el menú, y ser el mismo objeto es lo que hace obvio
                que lo devuelve. */}
            {collapsed && (
              <button
                type="button"
                className="brand-square brand-square-restore"
                onClick={toggle}
                aria-expanded={false}
                aria-controls="main-nav"
                aria-label="Mostrar el menú"
                title="Mostrar el menú"
              />
            )}
            <div className="topbar-left">
              {kicker && <span className="topbar-kicker">{kicker}</span>}
              <h1 className="screen-title">{title}</h1>
            </div>
          </div>
          {topbarRight && <div className="topbar-right">{topbarRight}</div>}
        </header>
        <div className="page-body">
          <motion.div
            variants={pageVariants}
            initial="initial"
            animate="animate"
            transition={{ duration: 0.24, ease: 'easeOut' }}
          >
            {children}
          </motion.div>
        </div>
      </main>
      <ToastStack />
    </div>
  )
}
