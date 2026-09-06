import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../store/auth'

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div
        style={{
          minHeight: '100dvh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--ink2)',
          fontFamily: 'Manrope, sans-serif',
        }}
      >
        Cargando…
      </div>
    )
  }

  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

export function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth()

  if (isLoading) return null

  if (!user) return <Navigate to="/login" replace />
  if (user.role !== 'admin') return <Navigate to="/catalog" replace />

  return <>{children}</>
}

export function GuestOnly({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth()

  if (isLoading) return null
  if (user) return <Navigate to="/catalog" replace />
  return <>{children}</>
}
