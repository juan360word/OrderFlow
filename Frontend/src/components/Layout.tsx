import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { Sidebar } from './Sidebar'
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
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="main-content">
        <header className="topbar">
          <div className="topbar-left">
            {kicker && <span className="topbar-kicker">{kicker}</span>}
            <h1 className="screen-title">{title}</h1>
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
