import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Providers } from './components/Providers'
import { GuestOnly, RequireAuth, RequireAdmin } from './components/Guard'
import { AuthScreen } from './screens/AuthScreen'
import { CatalogScreen } from './screens/CatalogScreen'
import { ProductDetailScreen } from './screens/ProductDetailScreen'
import { CartScreen } from './screens/CartScreen'
import { OrdersScreen } from './screens/OrdersScreen'
import { OrderDetailScreen } from './screens/OrderDetailScreen'
import { AdminProductsScreen } from './screens/AdminProductsScreen'
import { AdminInventoryScreen } from './screens/AdminInventoryScreen'
import { AdminMetricsScreen } from './screens/AdminMetricsScreen'

export default function App() {
  return (
    <Providers>
      <BrowserRouter>
        <Routes>
          {/* Public / guest-only */}
          <Route
            path="/login"
            element={
              <GuestOnly>
                <AuthScreen />
              </GuestOnly>
            }
          />

          {/* Authenticated customer + admin routes */}
          <Route
            path="/catalog"
            element={
              <RequireAuth>
                <CatalogScreen />
              </RequireAuth>
            }
          />
          <Route
            path="/products/:id"
            element={
              <RequireAuth>
                <ProductDetailScreen />
              </RequireAuth>
            }
          />
          <Route
            path="/cart"
            element={
              <RequireAuth>
                <CartScreen />
              </RequireAuth>
            }
          />
          <Route
            path="/orders"
            element={
              <RequireAuth>
                <OrdersScreen />
              </RequireAuth>
            }
          />
          <Route
            path="/orders/:id"
            element={
              <RequireAuth>
                <OrderDetailScreen />
              </RequireAuth>
            }
          />

          {/* Admin-only routes */}
          <Route
            path="/admin/metrics"
            element={
              <RequireAdmin>
                <AdminMetricsScreen />
              </RequireAdmin>
            }
          />
          <Route
            path="/admin/products"
            element={
              <RequireAdmin>
                <AdminProductsScreen />
              </RequireAdmin>
            }
          />
          <Route
            path="/admin/inventory"
            element={
              <RequireAdmin>
                <AdminInventoryScreen />
              </RequireAdmin>
            }
          />

          {/* Default redirect */}
          <Route path="/" element={<Navigate to="/catalog" replace />} />
          <Route path="*" element={<Navigate to="/catalog" replace />} />
        </Routes>
      </BrowserRouter>
    </Providers>
  )
}
