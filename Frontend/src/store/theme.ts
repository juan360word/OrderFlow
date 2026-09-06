import { createContext, useContext } from 'react'

export type ThemeState = {
  dark: boolean
  toggle: () => void
}

export const ThemeContext = createContext<ThemeState>({
  dark: false,
  toggle: () => {},
})

export function useTheme() {
  return useContext(ThemeContext)
}
