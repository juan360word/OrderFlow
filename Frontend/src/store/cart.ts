import { createContext, useContext } from 'react'
import type { ProductResponse } from '../lib/schemas'

export type CartItem = {
  product: ProductResponse
  quantity: number
}

export type CartState = {
  items: CartItem[]
  addItem: (product: ProductResponse, qty?: number) => void
  removeItem: (productId: string) => void
  updateQty: (productId: string, qty: number) => void
  clearCart: () => void
  total: number
  count: number
}

export const CartContext = createContext<CartState>({
  items: [],
  addItem: () => {},
  removeItem: () => {},
  updateQty: () => {},
  clearCart: () => {},
  total: 0,
  count: 0,
})

export function useCart() {
  return useContext(CartContext)
}
