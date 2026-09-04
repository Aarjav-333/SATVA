/**
 * The public ward-level hotspot map.
 *
 * Non-negotiable rule 4: public surfaces show areas, never named vendors. This
 * page consumes `/hotspots`, which returns ward aggregates positioned at the
 * ward's own representative point — there is no centroid, no merchant reference
 * and no device identifier anywhere in the payload, so there is nothing here
 * that could identify a trader even by accident.
 *
 * `suppressed_clusters` is shown deliberately. It is evidence that the
 * corroboration rules are actually operating: SATVA detected a pattern and
 * declined to publish it.
 */

import { useCallback, useEffect, useState } from 'react'

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
  SeverityBadge,
  Spinner,
  Stat,
} from '../components/ui'

export default function PublicMap() {
  const [data, setData] = useState(null)
  const [stats, setStats] = useState(null)
  const [windowDays, setWindowDays] = useState(30)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [hotspots, watchStats] = await Promise.all([
        api.hotspots(windowDays),
        api.watchStats(windowDays),
      ])
      setData(hotspots)
      setStats(watchStats)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [windowDays])

  useEffect(() => {
    load()
  }, [load])

  if (loading) return <Spinner label="Loading the public map…" />
  if (error) return <ErrorNotice error={error} onRetry={load} />

  const suppressed = stats?.suppressed_clusters ?? 0

  return (
    <div className="space-y-6">
      <PageHeader
        title="SATVA Watch"
        subtitle="Ward-level areas where several independent people recorded a confirmed strip reading."
      >
        <select
          aria-label="Time window"
          className="rounded-xl border border-line bg-white px-3 py-2 text-sm font-medium"
          value={windowDays}
          onChange={(e) => setWindowDays(Number(e.target.value))}
        >
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
      </PageHeader>

      <div className="rounded-xl border border-brand/25 bg-brand-soft px-4 py-3">
        <p className="text-sm leading-relaxed text-brand-dark">{data?.notice}</p>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Confirmed readings" value={data?.total_confirmed_readings ?? 0} />
        <Stat label="Wards shown" value={data?.wards?.length ?? 0} tone="brand" />
        <Stat
          label="Patterns withheld"
          value={suppressed}
          hint="Detected, but not corroborated by enough independent devices to publish"
        />
        <Stat label="Window" value={`${windowDays} days`} />
      </div>

      {data?.wards?.some((w) => w.contains_demo_data) && (
        <div className="flex items-center gap-3 rounded-xl border border-line bg-white px-4 py-3">
          <DemoDataBadge />
          <p className="text-sm text-ink-soft">
            This map includes seeded demonstration data and does not represent real findings.
          </p>
        </div>
      )}

      <Card className="overflow-hidden">
        <CardHeader
          title="Ward map"
          subtitle="Circles sit at the centre of a ward and are sized by the number of confirmed readings."
        />
        <div className="h-[460px] border-t border-line">
          <HotspotMap wards={data?.wards ?? []} />
        </div>
      </Card>

      <Card>
        <CardHeader title="Wards" />
        <div className="overflow-x-auto border-t border-line">
          <table className="w-full min-w-[640px]">
            <thead>
              <tr className="bg-canvas">
                <th className="th">Ward</th>
                <th className="th">District</th>
                <th className="th">Confirmed readings</th>
                <th className="th">Produce</th>
                <th className="th">Severity</th>
              </tr>
            </thead>
            <tbody>
              {(data?.wards ?? []).map((ward) => (
                <tr key={ward.ward_code}>
                  <td className="td">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">{ward.ward_name}</span>
                      {ward.contains_demo_data && <DemoDataBadge />}
                    </div>
                    <span className="text-xs text-ink-faint">{ward.ward_code}</span>
                  </td>
                  <td className="td">{ward.district}</td>
                  <td className="td font-semibold tabular-nums">
                    {ward.confirmed_reading_count}
                  </td>
                  <td className="td capitalize">{ward.crops.join(', ') || '—'}</td>
                  <td className="td">
                    <SeverityBadge severity={ward.highest_severity} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {(data?.wards ?? []).length === 0 && (
            <EmptyState
              title="No published wards"
              body="No cluster has met the independent-device corroboration threshold yet. That is the system working as intended, not an absence of data."
            />
          )}
        </div>
      </Card>

      <Card className="card-pad">
        <h3 className="text-sm font-bold">Why some patterns are not shown</h3>
        <p className="mt-2 text-sm leading-relaxed text-ink-soft">
          A cluster appears here only when several <em>different</em> devices independently
          recorded a confirmed chemical reading in the same area, spread over time. One person
          submitting many readings — accidentally or deliberately — cannot create a hotspot.
          That is what stops this map being used to damage a competitor, and it is why{' '}
          <span className="font-semibold text-ink">{suppressed}</span> detected pattern
          {suppressed === 1 ? ' is' : 's are'} currently being withheld.
        </p>
      </Card>

      <Disclaimer />
    </div>
  )
}
