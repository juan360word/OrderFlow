import { createContext, useContext } from 'react'
import type { UserResponse } from '../lib/schemas'

export type AuthState = {
  user: UserResponse | null
  isLoading: boolean
  setUser: (user: UserResponse | null) => void
  logout: () => void
}

export const AuthContext = createContext<AuthState>({
  user: null,
  isLoading: true,
  setUser: () => {},
  logout: () => {},
})

export function useAuth() {
  return useContext(AuthContext)
}
