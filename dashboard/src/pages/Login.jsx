/**
 * Dashboard sign-in.
 *
 * Authorization is enforced server-side; this form only obtains a token. A
 * signed-in retailer still receives 403 from officer endpoints — which is the
 * point. Hiding a route in the client is not access control.
 */

import { useState } from 'react'

import { login } from '../lib/api'
import { Disclaimer } from '../components/ui'

const DEMO_ACCOUNTS = [
  ['officer@satva.demo', 'Food Safety Officer'],
  ['retailer@satva.demo', 'Retailer (SATVA Shelf)'],
  ['farmer@satva.demo', 'Farmer / FPO'],
  ['admin@satva.demo', 'Administrator'],
]

const DEMO_PASSWORD = 'satva-demo-2026'

export default function Login({ onSignedIn }) {
  const [email, setEmail] = useState('officer@satva.demo')
  const [password, setPassword] = useState(DEMO_PASSWORD)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const submit = async (event) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const user = await login(email, password)
      onSignedIn(user)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <p className="text-3xl font-extrabold tracking-[0.25em] text-brand">SATVA</p>
          <p className="mt-1.5 text-sm text-ink-soft">
            Scan · Analyse · Trace · Verify · Alert
          </p>
        </div>

        <form onSubmit={submit} className="card card-pad space-y-4">
          <div>
            <label htmlFor="email" className="label">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1.5 w-full rounded-xl border border-line px-3.5 py-2.5 text-sm"
            />
          </div>

          <div>
            <label htmlFor="password" className="label">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1.5 w-full rounded-xl border border-line px-3.5 py-2.5 text-sm"
            />
          </div>

          {error && (
            <p className="rounded-lg bg-confirmed-soft px-3 py-2 text-sm text-confirmed">
              {error}
            </p>
          )}

          <button type="submit" className="btn-primary w-full" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>

          <div className="rounded-xl border border-line bg-canvas p-3">
            <p className="label mb-2">Demonstration accounts</p>
            <ul className="space-y-1">
              {DEMO_ACCOUNTS.map(([address, role]) => (
                <li key={address}>
                  <button
                    type="button"
                    onClick={() => {
                      setEmail(address)
                      setPassword(DEMO_PASSWORD)
                    }}
                    className="flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-left text-xs hover:bg-white"
                  >
                    <span className="font-mono text-ink-soft">{address}</span>
                    <span className="text-ink-faint">{role}</span>
                  </button>
                </li>
              ))}
            </ul>
            <p className="mt-2 px-2 text-[11px] leading-snug text-ink-faint">
              Password <span className="font-mono">{DEMO_PASSWORD}</span>. These accounts are
              created only by the demo seeder and exist only in a seeded environment.
            </p>
          </div>
        </form>

        <div className="mt-6">
          <Disclaimer />
        </div>
      </div>
    </div>
  )
}
