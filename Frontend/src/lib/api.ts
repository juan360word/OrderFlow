import type {
  ShippingAddress,
  TokenPair,
  UserResponse,
  ProductResponse,
  StockResponse,
  OrderResponse,
  OrderSummaryResponse,
  Page,
} from './schemas'

const BASE = '/api/v1'

// ─── Token storage ────────────────────────────────────────────────────────────

const ACCESS_KEY = 'of_access'
const REFRESH_KEY = 'of_refresh'

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_KEY)
}

export function setTokens(pair: TokenPair): void {
  localStorage.setItem(ACCESS_KEY, pair.access_token)
  localStorage.setItem(REFRESH_KEY, pair.refresh_token)
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY)
}

// ─── Core fetch wrapper ───────────────────────────────────────────────────────

type FetchOptions = Omit<RequestInit, 'body'> & {
  body?: unknown
  idempotencyKey?: string
  skipAuth?: boolean
}

/**
 * El backend responde los errores en formato RFC 7807 (Problem Details):
 *
 *   { type, title, status, detail, errors?: { campo: [mensaje] }, request_id }
 *
 * `detail` es una frase genérica ("The request payload is invalid."). Lo que
 * de verdad dice qué falló vive en `errors`, indexado por campo. Descartarlo
 * es lo que hacía que un 422 llegara al usuario como un mensaje sin
 * información. `requestId` permite cruzar el fallo con los logs del servidor.
 */
class ApiError extends Error {
  status: number
  detail: string
  code?: string
  errors?: Record<string, string[]>
  requestId?: string

  constructor(
    status: number,
    detail: string,
    code?: string,
    errors?: Record<string, string[]>,
    requestId?: string,
  ) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.code = code
    this.errors = errors
    this.requestId = requestId
  }

  /** Primer mensaje del servidor para un campo, si lo hay. */
  fieldError(field: string): string | undefined {
    return this.errors?.[field]?.[0]
  }
}

export { ApiError }

async function apiFetch<T>(path: string, opts: FetchOptions = {}): Promise<T> {
  const { body, idempotencyKey, skipAuth, ...rest } = opts

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((rest.headers as Record<string, string>) ?? {}),
  }

  if (!skipAuth) {
    const token = getAccessToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
  }

  if (idempotencyKey) {
    headers['Idempotency-Key'] = idempotencyKey
  }

  const res = await fetch(`${BASE}${path}`, {
    ...rest,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (res.status === 204) return undefined as T

  const data = await res.json().catch(() => ({}))

  if (!res.ok) {
    // 401 → clear tokens (let app redirect)
    if (res.status === 401) clearTokens()
    const detail = data?.detail ?? data?.message ?? 'Error desconocido'
    const code = data?.title ?? undefined
    const errors =
      data?.errors && typeof data.errors === 'object'
        ? (data.errors as Record<string, string[]>)
        : undefined
    throw new ApiError(res.status, detail, code, errors, data?.request_id)
  }

  return data as T
}

// ─── Auth ─────────────────────────────────────────────────────────────────────

export const authApi = {
  register: (body: { email: string; password: string; full_name: string }) =>
    apiFetch<UserResponse>('/auth/register', { method: 'POST', body, skipAuth: true }),

  login: (body: { email: string; password: string }) =>
    apiFetch<TokenPair>('/auth/login', { method: 'POST', body, skipAuth: true }),

  me: () => apiFetch<UserResponse>('/auth/me'),

  logout: (refresh_token: string) =>
    apiFetch<void>('/auth/logout', { method: 'POST', body: { refresh_token } }),

  refresh: (refresh_token: string) =>
    apiFetch<TokenPair>('/auth/refresh', {
      method: 'POST',
      body: { refresh_token },
      skipAuth: true,
    }),
}

// ─── Products ─────────────────────────────────────────────────────────────────

export const productsApi = {
  list: (params?: { search?: string; limit?: number; offset?: number; include_inactive?: boolean }) => {
    const q = new URLSearchParams()
    if (params?.search) q.set('search', params.search)
    if (params?.limit != null) q.set('limit', String(params.limit))
    if (params?.offset != null) q.set('offset', String(params.offset))
    if (params?.include_inactive) q.set('include_inactive', 'true')
    const qs = q.toString()
    return apiFetch<Page<ProductResponse>>(`/products${qs ? `?${qs}` : ''}`)
  },

  get: (id: string) => apiFetch<ProductResponse>(`/products/${id}`),

  /**
   * El catálogo entero, en páginas del tamaño que el backend admite.
   *
   * `list` no puede pedir más de 100 por página: el tope está puesto a
   * propósito, porque `?limit=1000000` sería una denegación de servicio de una
   * sola petición. Pedir más no devuelve menos, devuelve un 422 — que es
   * exactamente por qué las pantallas de administración salían vacías.
   *
   * Las vistas de administración necesitan la lista completa (una tabla, no un
   * catálogo paginado), así que aquí se recorre página a página hasta
   * completarla. `max` es un freno: sin él, un catálogo enorme se traería
   * entero al navegador sin que nadie lo hubiera pedido.
   */
  listAll: async (params?: {
    search?: string
    include_inactive?: boolean
    max?: number
  }): Promise<Page<ProductResponse>> => {
    const PAGE_SIZE = 100
    const { max = 1000, ...filters } = params ?? {}
    const items: ProductResponse[] = []
    let total = 0

    while (items.length < max) {
      const page = await productsApi.list({
        ...filters,
        limit: PAGE_SIZE,
        offset: items.length,
      })
      total = page.total
      items.push(...page.items)
      // Sin la primera condición, una página vacía dejaría el bucle girando
      // para siempre contra un `total` que nunca se alcanza.
      if (page.items.length === 0 || items.length >= total) break
    }

    return { items, total, limit: items.length, offset: 0 }
  },

  create: (body: {
    name: string
    sku: string
    price: number
    description?: string
    initial_stock?: number
    currency?: string
  }) => apiFetch<ProductResponse>('/products', { method: 'POST', body }),

  update: (id: string, body: Partial<{ name: string; description: string; price: number; is_active: boolean }>) =>
    apiFetch<ProductResponse>(`/products/${id}`, { method: 'PATCH', body }),

  /**
   * Dos operaciones detrás del mismo verbo, y la diferencia importa.
   *
   * Por defecto es una retirada: la fila sobrevive marcada inactiva, porque el
   * historial de pedidos apunta a ella. Con `permanent` la fila se borra de
   * verdad, y el backend responde 409 si el producto llegó a venderse alguna
   * vez — esa decisión es suya, no del cliente, porque solo la base de datos
   * puede tomarla sin carreras.
   */
  delete: (id: string, opts?: { permanent?: boolean }) =>
    apiFetch<void>(`/products/${id}${opts?.permanent ? '?permanent=true' : ''}`, {
      method: 'DELETE',
    }),

  /**
   * Sube la foto de un producto.
   *
   * Va por FormData y no por JSON, y por eso NO pasa por apiFetch: ese wrapper
   * fija `Content-Type: application/json`. En una subida multipart el navegador
   * tiene que poner el Content-Type él mismo, porque incluye el `boundary` que
   * separa las partes y que solo él conoce. Fijarlo a mano rompe la petición.
   */
  uploadImage: async (id: string, file: File): Promise<ProductResponse> => {
    const body = new FormData()
    body.append('file', file)

    const token = getAccessToken()
    const res = await fetch(`${BASE}/products/${id}/image`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body,
    })

    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      if (res.status === 401) clearTokens()
      throw new ApiError(
        res.status,
        res.status === 413
          ? 'La imagen es demasiado grande.'
          : (data?.detail ?? 'No se pudo subir la imagen.'),
        data?.title,
        data?.errors,
        data?.request_id,
      )
    }
    return data as ProductResponse
  },

  deleteImage: (id: string) =>
    apiFetch<void>(`/products/${id}/image`, { method: 'DELETE' }),
}

// ─── Inventory ────────────────────────────────────────────────────────────────

export const inventoryApi = {
  get: (productId: string) => apiFetch<StockResponse>(`/inventory/${productId}`),

  adjust: (productId: string, body: { delta: number; reason: string }) =>
    apiFetch<StockResponse>(`/inventory/${productId}/adjust`, { method: 'POST', body }),
}

// ─── Orders ───────────────────────────────────────────────────────────────────

/**
 * Lo que se envía al crear un pedido.
 *
 * Los campos opcionales se omiten en vez de mandarse vacíos: el backend trata
 * "" como ausente, pero no hace falta obligarle a limpiar lo que aquí ya se
 * sabe que está en blanco.
 */
export type ShippingAddressInput = Omit<ShippingAddress, 'line2' | 'postal_code' | 'notes'> & {
  line2?: string
  postal_code?: string
  notes?: string
}

export const ordersApi = {
  list: (params?: { status?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams()
    if (params?.status) q.set('status', params.status)
    if (params?.limit != null) q.set('limit', String(params.limit))
    if (params?.offset != null) q.set('offset', String(params.offset))
    const qs = q.toString()
    return apiFetch<Page<OrderSummaryResponse>>(`/orders${qs ? `?${qs}` : ''}`)
  },

  get: (id: string) => apiFetch<OrderResponse>(`/orders/${id}`),

  create: (
    body: {
      items: { product_id: string; quantity: number }[]
      shipping_address: ShippingAddressInput
    },
    idempotencyKey: string,
  ) =>
    apiFetch<OrderResponse>('/orders', {
      method: 'POST',
      body,
      idempotencyKey,
    }),

  cancel: (id: string, reason?: string) =>
    apiFetch<OrderResponse>(`/orders/${id}/cancel`, {
      method: 'POST',
      body: { reason: reason ?? null },
    }),

  confirm: (id: string) =>
    apiFetch<OrderResponse>(`/orders/${id}/confirm`, { method: 'POST' }),
}
