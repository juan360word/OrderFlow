import { createContext, useContext } from 'react'

export type SidebarState = {
  /** El menú está plegado y la tienda ocupa toda la pantalla. */
  collapsed: boolean
  toggle: () => void
}

export const SidebarContext = createContext<SidebarState>({
  collapsed: false,
  toggle: () => {},
})

export function useSidebar() {
  return useContext(SidebarContext)
}
