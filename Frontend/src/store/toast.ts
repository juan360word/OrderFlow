import { createContext, useContext } from 'react'

export type ToastType = 'ok' | 'bad' | 'warn'

export type Toast = {
  id: string
  msg: string
  type: ToastType
}

export type ToastState = {
  toasts: Toast[]
  addToast: (msg: string, type?: ToastType) => void
}

export const ToastContext = createContext<ToastState>({
  toasts: [],
  addToast: () => {},
})

export function useToast() {
  return useContext(ToastContext)
}
