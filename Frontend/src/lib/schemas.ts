import { z } from 'zod'

// ─── Auth ────────────────────────────────────────────────────────────────────

export const registerSchema = z.object({
  email: z.string().email('Correo inválido'),
  full_name: z.string().min(2, 'Mínimo 2 caracteres').max(120),
  password: z.string().min(8, 'Mínimo 8 caracteres').max(128),
})

export const loginSchema = z.object({
  email: z.string().email('Correo inválido'),
  password: z.string().min(1, 'Requerido'),
})

export const changePasswordSchema = z
  .object({
    current_password: z.string().min(1, 'Requerido'),
    new_password: z.string().min(8, 'Mínimo 8 caracteres').max(128),
    confirm_password: z.string(),
  })
  .refine((d) => d.new_password === d.confirm_password, {
    message: 'Las contraseñas no coinciden',
    path: ['confirm_password'],
  })

// ─── Products ────────────────────────────────────────────────────────────────

export const productCreateSchema = z.object({
  name: z.string().min(1, 'Requerido').max(200),
  sku: z
    .string()
    .min(3, 'Mínimo 3 caracteres')
    .max(64)
    .regex(/^[A-Z0-9][A-Z0-9\-_]{2,63}$/, 'Formato: ABC-01 (mayúsculas)'),
  price: z.coerce.number().positive('Debe ser mayor a 0'),
  description: z.string().max(5000).optional(),
  initial_stock: z.coerce.number().int().min(0).optional(),
})

export const productUpdateSchema = z.object({
  name: z.string().min(1).max(200).optional(),
  description: z.string().max(5000).optional(),
  price: z.coerce.number().positive().optional(),
  is_active: z.boolean().optional(),
})

// ─── Orders ──────────────────────────────────────────────────────────────────

export const orderCancelSchema = z.object({
  reason: z.string().max(255).optional(),
})

// ─── Inventory ───────────────────────────────────────────────────────────────

export const stockAdjustSchema = z.object({
  delta: z
    .string()
    .min(1, 'Requerido')
    .transform((v) => Number(v))
    .pipe(z.number().int().min(-1_000_000).max(1_000_000)),
  reason: z.string().min(3, 'Mínimo 3 caracteres').max(255),
})

// ─── API Response types ──────────────────────────────────────────────────────

export type TokenPair = {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export type UserResponse = {
  id: string
  email: string
  full_name: string
  role: 'customer' | 'admin'
  is_active: boolean
  created_at: string
  last_login_at: string | null
}

export type ProductResponse = {
  id: string
  sku: string
  name: string
  description: string
  price: string
  currency: string
  is_active: boolean
  created_at: string
  updated_at: string
}

export type StockResponse = {
  product_id: string
  quantity_available: number
  quantity_reserved: number
  quantity_total: number
  version: number
  updated_at: string
}

export type OrderStatus = 'pending' | 'confirmed' | 'cancelled'

export type OrderItemResponse = {
  product_id: string
  product_sku: string
  product_name: string
  quantity: number
  unit_price: string
  subtotal: string
}

export type OrderResponse = {
  id: string
  user_id: string
  status: OrderStatus
  total_amount: string
  currency: string
  items: OrderItemResponse[]
  created_at: string
  updated_at: string
  confirmed_at: string | null
  cancelled_at: string | null
  cancellation_reason: string | null
}

export type OrderSummaryResponse = {
  id: string
  user_id: string
  status: OrderStatus
  total_amount: string
  currency: string
  item_count: number
  created_at: string
}

export type Page<T> = {
  items: T[]
  total: number
  limit: number
  offset: number
}

export type InventoryRow = {
  product_id: string
  sku: string
  name: string
  quantity_available: number
  quantity_reserved: number
}
