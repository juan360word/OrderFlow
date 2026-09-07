import { NavLink, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  Package,
  Warehouse,
  ShoppingCart,
  ClipboardList,
  BookOpen,
  LogOut,
  Sun,
  Moon,
} from 'lucide-react'
import { useAuth } from '../store/auth'
import { useTheme } from '../store/theme'
import { useSidebar } from '../store/sidebar'
import { useCart } from '../store/cart'
import { authApi } from '../lib/api'

type NavEntry = {
  to: string
  label: string
  icon: React.ReactNode
  badge?: number
}

export function Sidebar() {
  const { user, logout } = useAuth()
  const { dark, toggle } = useTheme()
  const { collapsed, toggle: toggleSidebar } = useSidebar()
  const { count } = useCart()
  const navigate = useNavigate()

  const isAdmin = user?.role === 'admin'

  const customerNav: NavEntry[] = [
    { to: '/catalog', label: 'Catálogo', icon: <BookOpen size={17} /> },
    { to: '/cart', label: 'Mi pedido', icon: <ShoppingCart size={17} />, badge: count || undefined },
    { to: '/orders', label: 'Mis pedidos', icon: <ClipboardList size={17} /> },
  ]

  const adminNav: NavEntry[] = [
    { to: '/admin/metrics', label: 'Métricas', icon: <LayoutDashboard size={17} /> },
    { to: '/admin/products', label: 'Productos', icon: <Package size={17} /> },
    { to: '/admin/inventory', label: 'Inventario', icon: <Warehouse size={17} /> },
    { to: '/orders', label: 'Pedidos', icon: <ClipboardList size={17} /> },
    { to: '/catalog', label: 'Catálogo', icon: <BookOpen size={17} /> },
  ]

  const navItems = isAdmin ? adminNav : customerNav

  const initials = user?.full_name
    ? user.full_name
        .split(' ')
        .slice(0, 2)
        .map((w) => w[0]?.toUpperCase())
        .join('')
    : '?'

  async function handleLogout() {
    try {
      const rt = localStorage.getItem('of_refresh')
      if (rt) await authApi.logout(rt)
    } catch {
      // ignore
    } finally {
      logout()
      navigate('/login')
    }
  }

  return (
    <aside className="sidebar">
      {/* Brand. El cuadrado es el botón que pliega el menú: se queda en el
          sitio donde ya estaba, y al plegarse reaparece en la barra superior
          para poder volver. */}
      <div className="sidebar-brand">
        <button
          type="button"
          className="brand-square"
          onClick={toggleSidebar}
          aria-expanded={!collapsed}
          aria-controls="main-nav"
          aria-label="Ocultar el menú"
          title="Ocultar el menú"
        />
        <span className="brand-name">OrderFlow</span>
      </div>

      {/* Nav */}
      <nav className="sidebar-nav" id="main-nav">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
          >
            {item.icon}
            <span>{item.label}</span>
            {item.badge ? <span className="nav-badge">{item.badge}</span> : null}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="sidebar-footer">
        <div className="user-chip">
          <div className="user-avatar">{initials}</div>
          <div className="user-info">
            <div className="user-name">{user?.full_name ?? 'Usuario'}</div>
            <div className="user-role">{user?.role ?? ''}</div>
          </div>
        </div>
        <div className="footer-actions">
          <button className="btn btn-ghost btn-sm" style={{ flex: 1 }} onClick={toggle} type="button">
            {dark ? <Sun size={15} /> : <Moon size={15} />}
            <span>{dark ? 'Claro' : 'Oscuro'}</span>
          </button>
          <button className="btn btn-ghost btn-sm" style={{ flex: 1 }} onClick={handleLogout} type="button">
            <LogOut size={15} />
            <span>Salir</span>
          </button>
        </div>
      </div>
    </aside>
  )
}
