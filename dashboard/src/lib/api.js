/**
 * SATVA API client for the dashboards.
 *
 * The token lives in memory plus sessionStorage, not localStorage: an officer
 * session that survives a browser restart on a shared machine is a real risk,
 * and sessionStorage scopes it to the tab.
 */

const BASE = import.meta.env.VITE_SATVA_API_BASE || '/api/v1'
const TOKEN_KEY = 'satva.dashboard.token'
const USER_KEY = 'satva.dashboard.user'

let accessToken = sessionStorage.getItem(TOKEN_KEY) || null

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

function authHeaders() {
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {}
}

async function request(path, { method = 'GET', body, query } = {}) {
  const url = new URL(`${BASE}${path}`, window.location.origin)
  if (query) {
    Object.entries(query).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v)
    })
  }

  let response
  try {
    response = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch (err) {
    throw new ApiError(0, 'network_error', 'Could not reach the SATVA API.')
  }

  if (response.status === 204) return null

  let payload = null
  try {
    payload = await response.json()
  } catch {
    payload = null
  }

  if (!response.ok) {
    const error = payload?.error ?? {}
    // A 401 means the session is gone; clearing it here avoids every subsequent
    // call failing the same way with a stale token attached.
    if (response.status === 401) clearSession()
    throw new ApiError(
      response.status,
      error.code ?? 'http_error',
      error.message ?? `Request failed (${response.status})`,
      error.details,
    )
  }
  return payload
}

export function getToken() {
  return accessToken
}

export function getStoredUser() {
  const raw = sessionStorage.getItem(USER_KEY)
  return raw ? JSON.parse(raw) : null
}

export function clearSession() {
  accessToken = null
  sessionStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(USER_KEY)
}

export async function login(email, password) {
  const result = await request('/auth/login', {
    method: 'POST',
    body: { email, password },
  })
  accessToken = result.tokens.access_token
  sessionStorage.setItem(TOKEN_KEY, accessToken)
  sessionStorage.setItem(USER_KEY, JSON.stringify(result.user))
  return result.user
}

export const api = {
  // --- Public (no auth) -------------------------------------------------
  meta: () => request('/meta'),
  health: () => request('/health'),
  hotspots: (windowDays = 30, district) =>
    request('/hotspots', { query: { window_days: windowDays, district } }),
  watchStats: (windowDays = 30) =>
    request('/hotspots/stats', { query: { window_days: windowDays } }),

  // --- Officer (authenticated, audited) ---------------------------------
  worklist: (includeSuppressed = true) =>
    request('/officers/worklist', { query: { include_suppressed: includeSuppressed } }),
  cluster: (id) => request(`/officers/clusters/${id}`),
  officerComplaints: (status) => request('/officers/complaints', { query: { status } }),
  recomputeClusters: () => request('/officers/clusters/recompute', { method: 'POST' }),
  auditTrail: (limit = 50) => request('/officers/audit', { query: { limit } }),

  // --- Retail -----------------------------------------------------------
  outlets: () => request('/retail/outlets'),
  inventory: (outletId, action) =>
    request('/retail/inventory', { query: { outlet_id: outletId, action } }),
  shelfSummary: (outletId) => request('/retail/summary', { query: { outlet_id: outletId } }),
  spoilage: (outletId, windowDays = 30) =>
    request('/retail/spoilage', { query: { outlet_id: outletId, window_days: windowDays } }),
  predictItem: (itemId, ripeness) =>
    request(`/retail/inventory/${itemId}/predict`, {
      method: 'POST',
      query: { ripeness_index: ripeness },
    }),

  // --- Trace ------------------------------------------------------------
  lots: (crop) => request('/lots', { query: { crop } }),
  lot: (id) => request(`/lots/${id}`),
  verifyLot: (id) => request(`/lots/${id}/verify`),
  merkleRoot: () => request('/trace/merkle/latest'),

  // --- Marketplace ------------------------------------------------------
  listings: () => request('/marketplace/listings'),
  trustScore: (farmId) => request(`/marketplace/farms/${farmId}/trust-score`),
}
