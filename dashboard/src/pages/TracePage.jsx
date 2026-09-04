/**
 * SATVA Trace — lot provenance and chain integrity.
 *
 * The verification panel is the point of this page. A "verified" badge that
 * nobody can interrogate is just a claim; showing the head hash, the chain
 * length and the daily Merkle root lets a sceptical reader check the claim
 * themselves, which is the whole reason for using a hash chain rather than a
 * database flag.
 */

import { useCallback, useEffect, useState } from 'react'

import { api } from '../lib/api'
import {
  Card,
  CardHeader,
  DemoDataBadge,
  Disclaimer,
  EmptyState,
  ErrorNotice,
  PageHeader,
  Spinner,
} from '../components/ui'

const EVENT_LABELS = {
  lot_registered: 'Harvested and registered',
  aggregator_scan_in: 'Received at collection centre',
  aggregator_scan_out: 'Dispatched from collection centre',
  transport_depart: 'Left for market',
  transport_arrive: 'Arrived at market',
  temperature_reading: 'Temperature logged in transit',
  retailer_received: 'Received by retailer',
  lot_split: 'Divided into shelf units',
  lot_withdrawn: 'Withdrawn from sale',
}

export default function TracePage() {
  const [lots, setLots] = useState(null)
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [verification, setVerification] = useState(null)
  const [merkle, setMerkle] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [lotList, root] = await Promise.all([
        api.lots(),
        api.merkleRoot().catch(() => null),
      ])
      setLots(lotList)
      setMerkle(root)
      if (lotList.length > 0) setSelected(lotList[0].id)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    setDetail(null)
    setVerification(null)
    Promise.all([api.lot(selected), api.verifyLot(selected)])
      .then(([lot, verify]) => {
        if (cancelled) return
        setDetail(lot)
        setVerification(verify)
      })
      .catch((err) => {
        if (!cancelled) setError(err)
      })
    return () => {
      cancelled = true
    }
  }, [selected])

  if (loading) return <Spinner label="Loading produce lots…" />
  if (error && !lots) return <ErrorNotice error={error} onRetry={load} />

  return (
    <div className="space-y-6">
      <PageHeader
        title="SATVA Trace"
        subtitle="Tamper-evident custody from farm to shelf. A hash chain, not a blockchain — the requirement is tamper-evidence, not consensus."
      />

      {merkle && (
        <Card className="card-pad">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="label">Latest published Merkle root</p>
              <p className="mt-1 break-all font-mono text-xs text-ink-soft">
                {merkle.root_hash}
              </p>
              <p className="mt-1.5 text-xs text-ink-faint">
                {merkle.leaf_count} custody record{merkle.leaf_count === 1 ? '' : 's'} covered ·{' '}
                {new Date(merkle.period_date).toLocaleDateString('en-IN', {
                  day: '2-digit',
                  month: 'long',
                  year: 'numeric',
                })}
              </p>
            </div>
            <p className="max-w-md text-xs leading-relaxed text-ink-faint">
              Publishing a daily root means even SATVA&apos;s own operators cannot rewrite a
              covered custody record without the recomputed root diverging from the published
              one.
            </p>
          </div>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader title="Produce lots" />
          <div className="max-h-[560px] overflow-y-auto border-t border-line">
            {(lots ?? []).map((lot) => (
              <button
                key={lot.id}
                type="button"
                onClick={() => setSelected(lot.id)}
                className={`flex w-full items-start justify-between gap-3 border-b border-line px-4 py-3 text-left transition-colors hover:bg-canvas ${
                  selected === lot.id ? 'bg-brand-soft/50' : ''
                }`}
              >
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-semibold">{lot.lot_code}</span>
                    {lot.is_synthetic && <DemoDataBadge />}
                  </div>
                  <p className="mt-0.5 text-xs capitalize text-ink-soft">
                    {lot.crop}
                    {lot.cultivar ? ` · ${lot.cultivar.replace(/_/g, ' ')}` : ''}
                  </p>
                  <p className="text-[11px] text-ink-faint">
                    {Number(lot.quantity_kg).toFixed(0)} kg · {lot.event_count} events
                    {lot.parent_lot_id ? ' · split unit' : ''}
                  </p>
                </div>
              </button>
            ))}
            {(lots ?? []).length === 0 && (
              <EmptyState title="No lots registered" body="Register a lot to open a custody chain." />
            )}
          </div>
        </Card>

        <div className="space-y-6 lg:col-span-2">
          {detail && verification ? (
            <>
              <Card>
                <CardHeader
                  title="Chain integrity"
                  subtitle="Recomputed from the stored records, across the whole ancestry."
                  actions={
                    verification.valid ? (
                      <span className="badge bg-clear-soft text-clear">Verified</span>
                    ) : (
                      <span className="badge bg-confirmed-soft text-confirmed">Tampered</span>
                    )
                  }
                />
                <div className="border-t border-line p-5">
                  <div className="space-y-3">
                    {verification.lots.map((entry) => (
                      <div
                        key={entry.lot_id}
                        className="flex items-start justify-between gap-4 rounded-xl border border-line p-3"
                      >
                        <div className="min-w-0">
                          <p className="font-mono text-xs font-semibold">
                            {entry.lot_code ?? entry.lot_id.slice(0, 8)}
                          </p>
                          <p className="mt-0.5 text-xs text-ink-soft">
                            {entry.length} record{entry.length === 1 ? '' : 's'} verified
                          </p>
                          {entry.head_hash && (
                            <p className="mt-1 truncate font-mono text-[11px] text-ink-faint">
                              head {entry.head_hash.slice(0, 32)}…
                            </p>
                          )}
                          {entry.detail && (
                            <p className="mt-1 text-xs text-confirmed">{entry.detail}</p>
                          )}
                        </div>
                        <span
                          className={`badge shrink-0 ${
                            entry.valid
                              ? 'bg-clear-soft text-clear'
                              : 'bg-confirmed-soft text-confirmed'
                          }`}
                        >
                          {entry.valid ? 'OK' : entry.failure_kind ?? 'Failed'}
                        </span>
                      </div>
                    ))}
                  </div>

                  <p className="mt-4 text-xs leading-relaxed text-ink-faint">
                    Each record stores <span className="font-mono">SHA-256(payload ‖ previous_hash)</span>.
                    Altering any historical entry changes its hash and breaks every link after
                    it, so verification is a single pass from genesis to head.
                  </p>
                </div>
              </Card>

              <Card>
                <CardHeader
                  title="Journey"
                  subtitle={`${detail.lot_code} · ${detail.crop}`}
                />
                <ol className="border-t border-line p-5">
                  {detail.custody_events.map((event, index) => (
                    <li key={event.id} className="relative flex gap-4 pb-6 last:pb-0">
                      <div className="flex flex-col items-center">
                        <span className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full bg-brand" />
                        {index < detail.custody_events.length - 1 && (
                          <span className="mt-1 w-px flex-1 bg-line" />
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-baseline justify-between gap-2">
                          <p className="text-sm font-semibold">
                            {EVENT_LABELS[event.event_type] ??
                              event.event_type.replace(/_/g, ' ')}
                          </p>
                          <time className="text-xs text-ink-faint">
                            {new Date(event.occurred_at).toLocaleString('en-IN', {
                              day: '2-digit',
                              month: 'short',
                              hour: '2-digit',
                              minute: '2-digit',
                            })}
                          </time>
                        </div>
                        {(event.actor_display_name || event.location_name) && (
                          <p className="mt-0.5 text-xs text-ink-soft">
                            {event.actor_display_name}
                            {event.location_name ? ` · ${event.location_name}` : ''}
                          </p>
                        )}
                        <div className="mt-1 flex flex-wrap gap-3 text-[11px] text-ink-faint">
                          {event.quantity_kg != null && (
                            <span>{Number(event.quantity_kg).toFixed(1)} kg</span>
                          )}
                          {event.temperature_c != null && (
                            <span>{event.temperature_c.toFixed(1)} °C</span>
                          )}
                          <span className="font-mono">{event.event_hash.slice(0, 16)}…</span>
                        </div>
                      </div>
                    </li>
                  ))}
                </ol>
              </Card>
            </>
          ) : (
            <Spinner label="Loading custody chain…" />
          )}
        </div>
      </div>

      <Card className="card-pad">
        <h3 className="text-sm font-bold">Trace is optional</h3>
        <p className="mt-2 text-sm leading-relaxed text-ink-soft">
          Produce without a QR code can still be screened with SATVA Scan. A missing code never
          blocks anything — Trace adds provenance where a supply chain chooses to provide it,
          and its absence is not treated as a signal about the produce.
        </p>
      </Card>

      <Disclaimer />
    </div>
  )
}
