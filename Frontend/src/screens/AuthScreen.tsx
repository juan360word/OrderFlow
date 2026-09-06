import { useState } from 'react'
import { useForm } from 'react-hook-form'
import type { FieldValues, Path, UseFormReturn } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { motion, AnimatePresence } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import { z } from 'zod'
import { authApi, setTokens } from '../lib/api'
import { loginSchema, registerSchema } from '../lib/schemas'
import { useAuth } from '../store/auth'
import { ApiError } from '../lib/api'

type LoginForm = z.infer<typeof loginSchema>
type RegisterForm = z.infer<typeof registerSchema>

/**
 * Coloca los errores de validación del servidor bajo su campo.
 *
 * Zod ya valida en el cliente las mismas reglas que Pydantic, así que un 422
 * debería ser raro. Cuando ocurre - porque el servidor sabe algo que el
 * navegador no puede saber - el usuario merece verlo junto al campo culpable
 * y no como una frase suelta al pie del formulario.
 *
 * Devuelve true si consiguió ubicar al menos un error.
 */
function applyServerErrors<T extends FieldValues>(
  error: ApiError,
  form: UseFormReturn<T>,
  fields: readonly Path<T>[],
): boolean {
  let applied = false
  for (const field of fields) {
    const message = error.fieldError(field)
    if (message) {
      form.setError(field, { type: 'server', message })
      applied = true
    }
  }
  return applied
}

export function AuthScreen() {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [apiError, setApiError] = useState('')
  const { setUser } = useAuth()
  const navigate = useNavigate()

  // ─── Login form ────────────────────────────────────────────────────────────
  const loginForm = useForm<LoginForm>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: '', password: '' },
  })

  async function onLogin(data: LoginForm) {
    setApiError('')
    try {
      const tokens = await authApi.login({ email: data.email, password: data.password })
      setTokens(tokens)
      const me = await authApi.me()
      setUser(me)
      navigate('/catalog', { replace: true })
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 429) {
          setApiError('Demasiados intentos, espera un momento.')
        } else if (e.status === 401) {
          // El backend responde lo mismo para "no existe ese correo" y para
          // "contraseña incorrecta", a propósito: distinguirlos le diría a un
          // atacante qué correos están registrados.
          setApiError('Correo o contraseña incorrectos.')
        } else if (applyServerErrors(e, loginForm, ['email', 'password'])) {
          setApiError('Revisa los campos marcados.')
        } else {
          setApiError(e.detail)
        }
      } else {
        setApiError('No se pudo conectar con el servidor.')
      }
    }
  }

  // ─── Register form ─────────────────────────────────────────────────────────
  const registerForm = useForm<RegisterForm>({
    resolver: zodResolver(registerSchema),
    defaultValues: { email: '', full_name: '', password: '' },
  })

  async function onRegister(data: RegisterForm) {
    setApiError('')
    try {
      await authApi.register(data)
      // auto-login after register
      const tokens = await authApi.login({ email: data.email, password: data.password })
      setTokens(tokens)
      const me = await authApi.me()
      setUser(me)
      navigate('/catalog', { replace: true })
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 429) {
          setApiError('Demasiados intentos, espera un momento.')
        } else if (e.status === 409) {
          setApiError('Este correo ya está registrado.')
        } else if (
          applyServerErrors(e, registerForm, ['email', 'full_name', 'password'])
        ) {
          setApiError('Revisa los campos marcados.')
        } else {
          setApiError(e.detail)
        }
      } else {
        setApiError('No se pudo conectar con el servidor.')
      }
    }
  }

  function switchMode(m: 'login' | 'register') {
    setApiError('')
    loginForm.reset()
    registerForm.reset()
    setMode(m)
  }

  return (
    <div className="auth-screen">
      <motion.div
        className="auth-card"
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.24, ease: 'easeOut' }}
      >
        {/* Brand */}
        <div className="auth-brand">
          <div className="brand-square" />
          <span className="brand-name">OrderFlow</span>
        </div>

        <AnimatePresence mode="wait">
          <motion.div
            key={mode}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.18 }}
          >
            <h2 className="auth-title">
              {mode === 'login' ? 'Bienvenido de vuelta' : 'Crea tu cuenta'}
            </h2>
            <p className="auth-sub">
              {mode === 'login'
                ? 'Inicia sesión para continuar'
                : 'Regístrate para empezar'}
            </p>

            {mode === 'login' ? (
              <form
                className="auth-form"
                onSubmit={loginForm.handleSubmit(onLogin)}
                noValidate
              >
                <div className="input-group">
                  <label className="input-label" htmlFor="login-email">
                    Correo
                  </label>
                  <input
                    id="login-email"
                    type="email"
                    className="input"
                    placeholder="tu@correo.com"
                    autoComplete="email"
                    {...loginForm.register('email')}
                  />
                  <span className="input-error-msg">
                    {loginForm.formState.errors.email?.message}
                  </span>
                </div>

                <div className="input-group">
                  <label className="input-label" htmlFor="login-password">
                    Contraseña
                  </label>
                  <input
                    id="login-password"
                    type="password"
                    className="input"
                    placeholder="••••••••"
                    autoComplete="current-password"
                    {...loginForm.register('password')}
                  />
                  <span className="input-error-msg">
                    {loginForm.formState.errors.password?.message}
                  </span>
                </div>

                {apiError && (
                  <div className="auth-error-block" role="alert">
                    {apiError}
                  </div>
                )}

                <button
                  type="submit"
                  className="btn btn-primary"
                  style={{ width: '100%', marginTop: 4 }}
                  disabled={loginForm.formState.isSubmitting}
                >
                  {loginForm.formState.isSubmitting ? 'Entrando…' : 'Iniciar sesión'}
                </button>

                <p className="auth-switch">
                  ¿No tienes cuenta?{' '}
                  <button type="button" onClick={() => switchMode('register')}>
                    Regístrate
                  </button>
                </p>
              </form>
            ) : (
              <form
                className="auth-form"
                onSubmit={registerForm.handleSubmit(onRegister)}
                noValidate
              >
                <div className="input-group">
                  <label className="input-label" htmlFor="reg-name">
                    Nombre completo
                  </label>
                  <input
                    id="reg-name"
                    type="text"
                    className="input"
                    placeholder="Ana García"
                    autoComplete="name"
                    {...registerForm.register('full_name')}
                  />
                  <span className="input-error-msg">
                    {registerForm.formState.errors.full_name?.message}
                  </span>
                </div>

                <div className="input-group">
                  <label className="input-label" htmlFor="reg-email">
                    Correo
                  </label>
                  <input
                    id="reg-email"
                    type="email"
                    className="input"
                    placeholder="tu@correo.com"
                    autoComplete="email"
                    {...registerForm.register('email')}
                  />
                  <span className="input-error-msg">
                    {registerForm.formState.errors.email?.message}
                  </span>
                </div>

                <div className="input-group">
                  <label className="input-label" htmlFor="reg-password">
                    Contraseña
                  </label>
                  <input
                    id="reg-password"
                    type="password"
                    className="input"
                    placeholder="Mínimo 12 caracteres"
                    autoComplete="new-password"
                    {...registerForm.register('password')}
                  />
                  <span className="input-error-msg">
                    {registerForm.formState.errors.password?.message}
                  </span>
                </div>

                {apiError && (
                  <div className="auth-error-block" role="alert">
                    {apiError}
                  </div>
                )}

                <button
                  type="submit"
                  className="btn btn-primary"
                  style={{ width: '100%', marginTop: 4 }}
                  disabled={registerForm.formState.isSubmitting}
                >
                  {registerForm.formState.isSubmitting ? 'Registrando…' : 'Crear cuenta'}
                </button>

                <p className="auth-switch">
                  ¿Ya tienes cuenta?{' '}
                  <button type="button" onClick={() => switchMode('login')}>
                    Inicia sesión
                  </button>
                </p>
              </form>
            )}
          </motion.div>
        </AnimatePresence>

        {/* Test accounts */}
        <div className="test-accounts">
          <p className="test-accounts-title">Cuentas de prueba</p>
          <p className="test-account">admin@orderflow.io · Segura2024!XZ</p>
          <p className="test-account">cliente@orderflow.io · Segura2024!XZ</p>
        </div>
      </motion.div>
    </div>
  )
}
