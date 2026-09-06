import type {
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

class ApiError extends Error {
  status: number
  detail: string
  code?: string

  constructor(status: number, detail: string, code?: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.code = code
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
    const detail =
      data?.detail ?? data?.message ?? 'Error desconocido'
    const code = data?.title ?? undefined
    throw new ApiError(res.status, detail, code)
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

  delete: (id: string) => apiFetch<void>(`/products/${id}`, { method: 'DELETE' }),
}

// ─── Inventory ────────────────────────────────────────────────────────────────

export const inventoryApi = {
  get: (productId: string) => apiFetch<StockResponse>(`/inventory/${productId}`),

  adjust: (productId: string, body: { delta: number; reason: string }) =>
    apiFetch<StockResponse>(`/inventory/${productId}/adjust`, { method: 'POST', body }),
}

// ─── Orders ───────────────────────────────────────────────────────────────────

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
    body: { items: { product_id: string; quantity: number }[] },
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
