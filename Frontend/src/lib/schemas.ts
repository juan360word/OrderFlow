import { z } from 'zod'

// ─── Auth ────────────────────────────────────────────────────────────────────

// El backend rechaza estas mismas contraseñas (auth/schemas.py). Repetirlas
// aquí no es duplicación por descuido: sin ellas el usuario solo se entera del
// rechazo después de enviar el formulario, y con un mensaje genérico.
const COMMON_PASSWORDS = new Set([
  'password',
  'password123',
  'passwordpassword',
  '123456789012',
  'qwertyuiop123',
  'administrator',
  'letmeinplease',
  'iloveyou1234',
  'welcome12345',
  'changeme1234',
])

// Estas tres reglas son las de RegisterRequest en el backend. La longitud
// mínima es 12, no 8: ese desajuste era la causa de que el registro fallara
// con "The request payload is invalid" sin explicar nada.
export const registerSchema = z
  .object({
    email: z.string().email('Correo inválido'),
    full_name: z.string().min(2, 'Mínimo 2 caracteres').max(120),
    password: z
      .string()
      .min(12, 'Mínimo 12 caracteres')
      .max(128, 'Máximo 128 caracteres'),
  })
  .superRefine((data, ctx) => {
    if (COMMON_PASSWORDS.has(data.password.toLowerCase())) {
      ctx.addIssue({
        code: 'custom',
        path: ['password'],
        message: 'Esa contraseña es demasiado común',
      })
      return
    }

    if (new Set(data.password).size < 5) {
      ctx.addIssue({
        code: 'custom',
        path: ['password'],
        message: 'Usa al menos 5 caracteres distintos',
      })
      return
    }

    const localPart = data.email.split('@')[0]?.toLowerCase() ?? ''
    if (localPart.length >= 4 && data.password.toLowerCase().includes(localPart)) {
      ctx.addIssue({
        code: 'custom',
        path: ['password'],
        message: 'La contraseña no puede contener tu correo',
      })
    }
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
  /** Ruta de la foto, o null si el producto no tiene. La sirve el backend. */
  image_url: string | null
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

export type ShippingAddress = {
  recipient_name: string
  phone: string
  line1: string
  line2: string | null
  city: string
  region: string
  postal_code: string | null
  /** ISO 3166-1 alpha-2, en mayúsculas. */
  country: string
  notes: string | null
}

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
  /** Nulo solo en pedidos anteriores a que existieran las direcciones. */
  shipping_address: ShippingAddress | null
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
