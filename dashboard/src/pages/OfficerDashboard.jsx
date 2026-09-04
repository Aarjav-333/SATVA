/**
 * Food Safety Officer dashboard.
 *
 * This is the only surface in SATVA that shows vendor-level detail
 * (non-negotiable rule 5). Access is enforced server-side and every view of a
 * cluster's detail is written to the audit log — the client cannot bypass
 * either by hiding a route.
 *
 * Two design decisions worth stating:
 *
 * 1. **Suppressed clusters are shown, with their reason.** An officer needs to
 *    see a pattern that has not yet met the corroboration threshold; the public
 *    map does not. Hiding them here would waste the signal, and showing them
 *    without the reason would be misleading.
 * 2. **The worklist is ordered by inspection priority, not by severity.** The
 *    aim in the specification is a better hit rate than random sampling, which
 *    means ranking by "how likely is an inspection here to find something",
 *    not by "how alarming does this look".
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { api } from '../lib/api'
import HotspotMap from '../components/HotspotMap'
import {
  Card,
  CardHeader,
  DemoDataBadge,
  Disclaimer,
  EmptyState,
  ErrorNotice,
  PageHeader,
  PublishedBadge,
  SeverityBadge,
  Spinner,
  Stat,
} from '../components/ui'

const CHART_COLOURS = {
  high: '#B42318',
  elevated: '#B54708',
  advisory: '#5B7FBE',
}

export default function OfficerDashboard() {
  const [worklist, setWorklist] = useState(null)
  const [stats, setStats] = useState(null)
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [recomputing, setRecomputing] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [clusters, watchStats] = await Promise.all([
        api.worklist(true),
        api.watchStats(30),
      ])
      setWorklist(clusters)
      setStats(watchStats)
      if (clusters.length > 0) setSelected(clusters[0].id)
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
    api
      .cluster(selected)
      .then((data) => {
        if (!cancelled) setDetail(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err)
      })
    return () => {
      cancelled = true
    }
  }, [selected])

  const recompute = async () => {
    setRecomputing(true)
    try {
      await api.recomputeClusters()
      await load()
    } catch (err) {
      setError(err)
    } finally {
      setRecomputing(false)
    }
  }

  const chartData = useMemo(() => {
    if (!worklist) return []
    return worklist
      .slice(0, 8)
      .map((c) => ({
        name: c.ward_name ?? c.ward_code ?? 'Unmapped',
        priority: c.inspection_priority,
        severity: c.severity,
      }))
  }, [worklist])

  if (loading) return <Spinner label="Loading the inspection worklist…" />
  if (error && !worklist) return <ErrorNotice error={error} onRetry={load} />

  const publishable = worklist?.filter((c) => c.is_publishable).length ?? 0
  const withheld = (worklist?.length ?? 0) - publishable

  return (
    <div className="space-y-6">
      <PageHeader
        title="Inspection worklist"
        subtitle="Clusters of confirmed strip readings, ranked by how likely an inspection is to find something."
      >
        <button className="btn-ghost" onClick={recompute} disabled={recomputing}>
          {recomputing ? 'Recomputing…' : 'Recompute clusters'}
        </button>
      </PageHeader>

      {error && <ErrorNotice error={error} />}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat
          label="Confirmed readings"
          value={stats?.confirmed_readings ?? '—'}
          hint="Strip-confirmed and shared in the last 30 days"
        />
        <Stat
          label="On the public map"
          value={publishable}
          tone="brand"
          hint="Met the independent-device threshold"
        />
        <Stat
          label="Withheld"
          value={withheld}
          tone="caution"
          hint="Detected but not corroborated enough to publish"
        />
        <Stat
          label="Wards covered"
          value={stats?.wards_covered ?? '—'}
          hint="Distinct wards with a published cluster"
        />
      </div>

      {stats?.demo_data_included && (
        <div className="flex items-center gap-3 rounded-xl border border-line bg-white px-4 py-3">
          <DemoDataBadge />
          <p className="text-sm text-ink-soft">
            This environment contains seeded demonstration data. Rows marked with the badge
            above are not collected evidence.
          </p>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <Card className="overflow-hidden">
            <CardHeader
              title="Hotspot map"
              subtitle="Officer view — shows cluster centroids and withheld clusters."
            />
            <div className="h-[420px] border-t border-line">
              <HotspotMap
                clusters={worklist ?? []}
                selectedId={selected}
                onSelect={setSelected}
                officerView
              />
            </div>
          </Card>
        </div>

        <div className="lg:col-span-2">
          <Card>
            <CardHeader title="Top priorities" subtitle="Ranked by inspection priority" />
            <div className="h-[420px] border-t border-line px-2 pt-4 pb-2">
              {chartData.length === 0 ? (
                <EmptyState title="No clusters yet" body="Run the clustering job to populate the worklist." />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} layout="vertical" margin={{ left: 8, right: 16 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#E4E7EC" horizontal={false} />
                    <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 11 }} stroke="#98A2B3" />
                    <YAxis
                      type="category"
                      dataKey="name"
                      width={110}
                      tick={{ fontSize: 11 }}
                      stroke="#98A2B3"
                    />
                    <Tooltip
                      cursor={{ fill: '#F7F8FA' }}
                      contentStyle={{
                        borderRadius: 12,
                        border: '1px solid #E4E7EC',
                        fontSize: 12,
                      }}
                      formatter={(value) => [`${value.toFixed(1)}`, 'Priority']}
                    />
                    <Bar dataKey="priority" radius={[0, 6, 6, 0]} barSize={18}>
                      {chartData.map((entry, index) => (
                        <Cell key={index} fill={CHART_COLOURS[entry.severity] ?? '#5B7FBE'} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader
          title="Clusters"
          subtitle="Withheld clusters are shown here with the reason they are not on the public map."
        />
        <div className="overflow-x-auto border-t border-line">
          <table className="w-full min-w-[880px]">
            <thead>
              <tr className="bg-canvas">
                <th className="th">Ward</th>
                <th className="th">Severity</th>
                <th className="th">Priority</th>
                <th className="th">Readings</th>
                <th className="th">Devices</th>
                <th className="th">Above threshold</th>
                <th className="th">Public map</th>
              </tr>
            </thead>
            <tbody>
              {(worklist ?? []).map((cluster) => (
                <tr
                  key={cluster.id}
                  onClick={() => setSelected(cluster.id)}
                  className={`cursor-pointer transition-colors hover:bg-canvas ${
                    selected === cluster.id ? 'bg-brand-soft/50' : ''
                  }`}
                >
                  <td className="td">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">{cluster.ward_name ?? 'Unmapped area'}</span>
                      {cluster.contains_demo_data && <DemoDataBadge />}
                    </div>
                    <span className="text-xs text-ink-faint">
                      {cluster.ward_code} · {cluster.district} · {cluster.crop ?? 'mixed'}
                    </span>
                  </td>
                  <td className="td"><SeverityBadge severity={cluster.severity} /></td>
                  <td className="td font-semibold tabular-nums">
                    {cluster.inspection_priority.toFixed(1)}
                  </td>
                  <td className="td tabular-nums">{cluster.member_count}</td>
                  <td className="td tabular-nums">{cluster.independent_device_count}</td>
                  <td className="td tabular-nums">
                    {(cluster.exceedance_rate * 100).toFixed(0)}%
                  </td>
                  <td className="td">
                    <PublishedBadge
                      isPublishable={cluster.is_publishable}
                      suppressionReason={cluster.suppression_reason}
                    />
                    {cluster.suppression_reason && (
                      <p className="mt-1 max-w-[220px] text-xs leading-snug text-ink-faint">
                        {cluster.suppression_reason.replace(/_/g, ' ')}
                      </p>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {(worklist ?? []).length === 0 && (
            <EmptyState
              title="No clusters"
              body="No confirmed readings have clustered in the current window."
            />
          )}
        </div>
      </Card>

      {selected && <ClusterDetail detail={detail} />}

      <Disclaimer />
    </div>
  )
}

function ClusterDetail({ detail }) {
  if (!detail) return <Spinner label="Loading cluster detail…" />

  return (
    <Card>
      <CardHeader
        title={`${detail.ward_name ?? 'Unmapped area'} — evidence`}
        subtitle="Vendor-level detail. This view is recorded in the audit log."
        actions={<SeverityBadge severity={detail.severity} />}
      />

      <div className="grid gap-6 border-t border-line p-5 lg:grid-cols-2">
        <div>
          <h3 className="mb-3 text-sm font-bold">Food business operators</h3>
          {detail.merchants.length === 0 ? (
            <p className="text-sm text-ink-soft">
              No vendor was identified by the people who submitted these readings. The
              location details below are what is available.
            </p>
          ) : (
            <ul className="space-y-3">
              {detail.merchants.map((m) => (
                <li key={m.merchant_ref} className="rounded-xl border border-line p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-semibold">{m.name}</p>
                      <p className="text-xs text-ink-soft">
                        {m.market_name} {m.stall_identifier && `· ${m.stall_identifier}`}
                      </p>
                      <p className="mt-1 text-xs text-ink-faint">{m.address_line}</p>
                      {m.fssai_licence_no && (
                        <p className="mt-1 font-mono text-[11px] text-ink-faint">
                          FSSAI {m.fssai_licence_no}
                        </p>
                      )}
                    </div>
                    <div className="text-right">
                      <p className="text-lg font-bold tabular-nums">
                        {m.confirmed_reading_count}
                      </p>
                      <p className="text-[11px] text-ink-faint">readings</p>
                    </div>
                  </div>
                  {m.is_synthetic && <DemoDataBadge className="mt-2" />}
                </li>
              ))}
            </ul>
          )}

          <div className="mt-5 rounded-xl border border-line bg-canvas p-4">
            <h4 className="text-xs font-bold uppercase tracking-wide text-ink-faint">
              Corroboration
            </h4>
            <dl className="mt-2 space-y-1 text-sm">
              <div className="flex justify-between">
                <dt className="text-ink-soft">Independent devices</dt>
                <dd className="font-semibold tabular-nums">
                  {detail.independent_device_count}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-ink-soft">Total readings</dt>
                <dd className="font-semibold tabular-nums">{detail.member_count}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-ink-soft">Mean concentration</dt>
                <dd className="font-semibold tabular-nums">
                  {detail.mean_concentration?.toFixed(2) ?? '—'}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-ink-soft">Cluster radius</dt>
                <dd className="font-semibold tabular-nums">{detail.radius_m.toFixed(0)} m</dd>
              </div>
            </dl>
          </div>
        </div>

        <div>
          <h3 className="mb-3 text-sm font-bold">
            Confirmed readings ({detail.members.length})
          </h3>
          <div className="max-h-[360px] overflow-y-auto rounded-xl border border-line">
            <table className="w-full">
              <thead className="sticky top-0 bg-canvas">
                <tr>
                  <th className="th">Captured</th>
                  <th className="th">Reading</th>
                  <th className="th">Device</th>
                </tr>
              </thead>
              <tbody>
                {detail.members.map((m) => (
                  <tr key={m.scan_id}>
                    <td className="td whitespace-nowrap text-xs">
                      {new Date(m.captured_at).toLocaleDateString('en-IN', {
                        day: '2-digit',
                        month: 'short',
                      })}
                      <span className="block text-ink-faint">{m.crop}</span>
                    </td>
                    <td className="td">
                      {m.concentration_value != null ? (
                        <span
                          className={`font-semibold tabular-nums ${
                            m.exceeds_action_threshold ? 'text-confirmed' : 'text-clear'
                          }`}
                        >
                          {m.concentration_value.toFixed(2)}
                        </span>
                      ) : (
                        '—'
                      )}
                      {m.exceeds_action_threshold && (
                        <span className="ml-1.5 text-[10px] font-semibold uppercase text-confirmed">
                          over
                        </span>
                      )}
                    </td>
                    <td className="td">
                      {/* The pseudonym is shown, never a user. It exists so an
                          officer can see how many distinct devices contributed
                          — the corroboration signal — without learning who. */}
                      <span className="font-mono text-[11px] text-ink-faint">
                        {m.device_pseudonym.slice(0, 12)}…
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="mt-3 text-xs leading-relaxed text-ink-faint">
            Device identifiers are keyed pseudonyms. They allow independent-device
            corroboration to be counted without SATVA linking a reading to a person.
          </p>
        </div>
      </div>
    </Card>
  )
}
