/**
 * Dashboard shell and navigation.
 *
 * Navigation is filtered by role for usability, not for security. The server
 * enforces authorization on every request, so a user who guesses a URL still
 * receives 403 — the menu simply avoids showing people doors they cannot open.
 */

import { Suspense, lazy, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { clearSession, getStoredUser, getToken } from './lib/api'
import Login from './pages/Login'
import { Disclaimer, Spinner } from './components/ui'

// Routes are lazy so MapLibre (~800 kB) and Recharts (~410 kB) are fetched only
// when a page that needs them is actually opened. A district office on a slow
// connection should not download a mapping engine to read a table.
const PublicMap = lazy(() => import('./pages/PublicMap'))
const OfficerDashboard = lazy(() => import('./pages/OfficerDashboard'))
const RetailDashboard = lazy(() => import('./pages/RetailDashboard'))
const TracePage = lazy(() => import('./pages/TracePage'))

const NAV = [
  { to: '/watch', label: 'Public map', roles: null },
  { to: '/officer', label: 'Officer worklist', roles: ['officer', 'admin'] },
  { to: '/retail', label: 'SATVA Shelf', roles: ['retailer', 'officer', 'admin'] },
  { to: '/trace', label: 'Trace', roles: null },
]

export default function App() {
  const [user, setUser] = useState(getStoredUser())
  const location = useLocation()

  const signedIn = Boolean(getToken()) && Boolean(user)

  // The public map is genuinely public — it is the surface a citizen sees, and
  // requiring a login for it would defeat its purpose.
  const isPublicRoute = location.pathname.startsWith('/watch') ||
    location.pathname.startsWith('/trace')

  if (!signedIn && !isPublicRoute) {
    return <Login onSignedIn={setUser} />
  }

  const visibleNav = NAV.filter(
    (item) => item.roles === null || (user && item.roles.includes(user.role)),
  )

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-line bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] items-center gap-6 px-5 py-3">
          <NavLink to="/watch" className="shrink-0">
            <span className="text-lg font-extrabold tracking-[0.2em] text-brand">SATVA</span>
          </NavLink>

          <nav className="flex flex-1 items-center gap-1 overflow-x-auto">
            {visibleNav.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-semibold transition-colors ${
                    isActive ? 'bg-brand-soft text-brand-dark' : 'text-ink-soft hover:bg-canvas'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          {signedIn ? (
            <div className="flex shrink-0 items-center gap-3">
              <div className="hidden text-right sm:block">
                <p className="text-xs font-semibold leading-tight">{user.display_name}</p>
                <p className="text-[11px] capitalize leading-tight text-ink-faint">
                  {user.role}
                  {user.officer_jurisdiction ? ` · ${user.officer_jurisdiction}` : ''}
                </p>
              </div>
              <button
                type="button"
                className="btn-ghost px-3 py-1.5 text-xs"
                onClick={() => {
                  clearSession()
                  setUser(null)
                }}
              >
                Sign out
              </button>
            </div>
          ) : (
            <NavLink to="/officer" className="btn-primary shrink-0 px-3 py-1.5 text-xs">
              Sign in
            </NavLink>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] px-5 py-7">
        <Suspense fallback={<Spinner />}>
          <Routes>
            <Route path="/" element={<Navigate to="/watch" replace />} />
            <Route path="/watch" element={<PublicMap />} />
            <Route path="/trace" element={<TracePage />} />
            <Route path="/officer" element={<OfficerDashboard />} />
            <Route path="/retail" element={<RetailDashboard />} />
            <Route
              path="*"
              element={
                <div className="py-20 text-center">
                  <p className="text-lg font-semibold">Page not found</p>
                  <NavLink to="/watch" className="btn-ghost mt-4 inline-flex">
                    Back to the map
                  </NavLink>
                </div>
              }
            />
          </Routes>
        </Suspense>
      </main>

      <footer className="mx-auto max-w-[1400px] px-5 pb-10">
        <Disclaimer />
        <p className="mt-3 text-center text-[11px] text-ink-faint">
          SATVA · Scan · Analyse · Trace · Verify · Alert · Team Real Fighters
        </p>
      </footer>
    </div>
  )
}
