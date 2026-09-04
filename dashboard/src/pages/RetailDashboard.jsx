/**
 * SATVA Shelf — the retailer dashboard.
 *
 * The commercial module: freshness, remaining shelf life, and what to do about
 * it today. Every prediction carries its rationale, because a shop manager
 * asked to discount stock is entitled to know why, and an unexplained
 * recommendation gets ignored.
 */

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

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
  Stat,
} from '../components/ui'

const ACTION_META = {
  sell_normally: { label: 'Sell normally', colour: '#067647', bg: 'bg-clear-soft text-clear' },
  prioritise: { label: 'Prioritise', colour: '#5B7FBE', bg: 'bg-unsure-soft text-unsure' },
  markdown: { label: 'Mark down', colour: '#B54708', bg: 'bg-caution-soft text-caution' },
  donate: { label: 'Donate', colour: '#7A5AF8', bg: 'bg-neutral2-soft text-neutral2' },
  withdraw: { label: 'Withdraw', colour: '#B42318', bg: 'bg-confirmed-soft text-confirmed' },
}

export default function RetailDashboard() {
  const [summary, setSummary] = useState(null)
  const [inventory, setInventory] = useState(null)
  const [spoilage, setSpoilage] = useState(null)
  const [expanded, setExpanded] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [s, items, waste] = await Promise.all([
        api.shelfSummary(),
        api.inventory(),
        api.spoilage(undefined, 45),
      ])
      setSummary(s)
      setInventory(items)
      setSpoilage(waste)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const actionData = useMemo(() => {
    if (!summary) return []
    return Object.entries(summary.action_counts)
      .filter(([, count]) => count > 0)
      .map(([action, count]) => ({
        name: ACTION_META[action]?.label ?? action,
        value: count,
        colour: ACTION_META[action]?.colour ?? '#98A2B3',
      }))
  }, [summary])

  const wasteData = useMemo(() => {
    if (!spoilage) return []
    return spoilage.points.map((p) => ({
      day: new Date(p.day).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }),
      Discarded: Number(p.discarded_kg.toFixed(1)),
      Donated: Number(p.donated_kg.toFixed(1)),
      'Marked down': Number(p.marked_down_kg.toFixed(1)),
    }))
  }, [spoilage])

  const needsAttention = useMemo(() => {
    if (!inventory) return []
    return inventory
      .filter((i) =>
        ['markdown', 'donate', 'withdraw'].includes(i.latest_prediction?.recommended_action),
      )
      .sort(
        (a, b) =>
          (a.latest_prediction?.remaining_shelf_life_days ?? 99) -
          (b.latest_prediction?.remaining_shelf_life_days ?? 99),
      )
  }, [inventory])

  if (loading) return <Spinner label="Loading shelf intelligence…" />
  if (error) return <ErrorNotice error={error} onRetry={load} />

  return (
    <div className="space-y-6">
      <PageHeader
        title="SATVA Shelf"
        subtitle={summary?.outlet?.name ?? 'Freshness and shelf-life intelligence'}
      >
        <button className="btn-ghost" onClick={load}>Refresh</button>
      </PageHeader>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Items in stock" value={summary?.total_items ?? 0} />
        <Stat
          label="Total weight"
          value={`${(summary?.total_kg ?? 0).toFixed(0)} kg`}
        />
        <Stat
          label="Needs action today"
          value={needsAttention.length}
          tone={needsAttention.length > 0 ? 'caution' : 'clear'}
          hint="Mark down, donate or withdraw"
        />
        <Stat
          label="Value at risk"
          value={`₹${(summary?.at_risk_value_inr ?? 0).toLocaleString('en-IN', {
            maximumFractionDigits: 0,
          })}`}
          tone="caution"
          hint="Stock that will not sell at full price"
        />
      </div>

      {summary?.demo_data_included && (
        <div className="flex items-center gap-3 rounded-xl border border-line bg-white px-4 py-3">
          <DemoDataBadge />
          <p className="text-sm text-ink-soft">
            This outlet is populated with seeded demonstration data.
          </p>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader title="Today's actions" subtitle="What the shelf model recommends" />
          <div className="h-[280px] border-t border-line p-3">
            {actionData.length === 0 ? (
              <EmptyState title="No predictions yet" body="Add stock to see recommendations." />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={actionData}
                    dataKey="value"
                    nameKey="name"
                    innerRadius={52}
                    outerRadius={82}
                    paddingAngle={2}
                    strokeWidth={0}
                  >
                    {actionData.map((entry, i) => (
                      <Cell key={i} fill={entry.colour} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{ borderRadius: 12, border: '1px solid #E4E7EC', fontSize: 12 }}
                    formatter={(v, n) => [`${v} item${v === 1 ? '' : 's'}`, n]}
                  />
                  <Legend
                    verticalAlign="bottom"
                    iconType="circle"
                    wrapperStyle={{ fontSize: 12 }}
                  />
                </PieChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader
            title="Waste over 45 days"
            subtitle="Discarded, donated and marked down"
          />
          <div className="h-[280px] border-t border-line p-3">
            {wasteData.length === 0 ? (
              <EmptyState title="No spoilage recorded" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={wasteData} margin={{ top: 8, right: 12, left: -18, bottom: 0 }}>
                  <defs>
                    {[
                      ['gDiscard', '#B42318'],
                      ['gDonate', '#7A5AF8'],
                      ['gMark', '#B54708'],
                    ].map(([id, colour]) => (
                      <linearGradient key={id} id={id} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={colour} stopOpacity={0.28} />
                        <stop offset="100%" stopColor={colour} stopOpacity={0.02} />
                      </linearGradient>
                    ))}
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E4E7EC" vertical={false} />
                  <XAxis
                    dataKey="day"
                    tick={{ fontSize: 10 }}
                    stroke="#98A2B3"
                    interval={Math.ceil(wasteData.length / 8)}
                  />
                  <YAxis tick={{ fontSize: 11 }} stroke="#98A2B3" unit="kg" />
                  <Tooltip
                    contentStyle={{ borderRadius: 12, border: '1px solid #E4E7EC', fontSize: 12 }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" />
                  <Area type="monotone" dataKey="Discarded" stroke="#B42318" fill="url(#gDiscard)" strokeWidth={2} />
                  <Area type="monotone" dataKey="Donated" stroke="#7A5AF8" fill="url(#gDonate)" strokeWidth={2} />
                  <Area type="monotone" dataKey="Marked down" stroke="#B54708" fill="url(#gMark)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Inventory"
          subtitle="Click a row to see exactly how the recommendation was reached."
        />
        <div className="overflow-x-auto border-t border-line">
          <table className="w-full min-w-[900px]">
            <thead>
              <tr className="bg-canvas">
                <th className="th">Item</th>
                <th className="th">Freshness</th>
                <th className="th">Shelf life left</th>
                <th className="th">Temperature</th>
                <th className="th">Trace</th>
                <th className="th">Action</th>
              </tr>
            </thead>
            <tbody>
              {(inventory ?? []).map((item) => {
                const p = item.latest_prediction
                const meta = ACTION_META[p?.recommended_action] ?? ACTION_META.sell_normally
                const isOpen = expanded === item.id
                return (
                  <Fragment key={item.id}>
                    <tr
                      onClick={() => setExpanded(isOpen ? null : item.id)}
                      className="cursor-pointer hover:bg-canvas"
                    >
                      <td className="td">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold capitalize">{item.crop}</span>
                          {item.is_synthetic && <DemoDataBadge />}
                        </div>
                        <span className="text-xs text-ink-faint">
                          {item.sku} · {Number(item.quantity_kg).toFixed(1)} kg ·{' '}
                          {item.display_location}
                        </span>
                      </td>
                      <td className="td">
                        {p?.freshness_score != null ? (
                          <FreshnessBar value={p.freshness_score} />
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="td">
                        {p?.remaining_shelf_life_days != null ? (
                          <>
                            <span className="font-semibold tabular-nums">
                              {p.remaining_shelf_life_days.toFixed(1)} d
                            </span>
                            <span className="block text-xs text-ink-faint">
                              {p.confidence_low_days?.toFixed(1)}–
                              {p.confidence_high_days?.toFixed(1)} d
                            </span>
                          </>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="td">
                        {p?.mean_temperature_c != null ? (
                          <>
                            <span className="tabular-nums">
                              {p.mean_temperature_c.toFixed(1)} °C
                            </span>
                            <span className="block text-xs text-ink-faint">
                              {p.temperature_source === 'ble_tag' ? 'BLE tag' : 'assumed ambient'}
                            </span>
                          </>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="td">
                        <TraceBadge status={item.trace_status} lotCode={item.lot_code} />
                      </td>
                      <td className="td">
                        <span className={`badge ${meta.bg}`}>{meta.label}</span>
                        {p?.suggested_markdown_pct != null && (
                          <span className="ml-2 text-xs font-semibold text-caution">
                            −{p.suggested_markdown_pct.toFixed(0)}%
                          </span>
                        )}
                      </td>
                    </tr>
                    {isOpen && p?.rationale && (
                      <tr>
                        <td colSpan={6} className="border-t border-line bg-canvas px-4 py-4">
                          <RationalePanel rationale={p.rationale} prediction={p} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
          {(inventory ?? []).length === 0 && (
            <EmptyState title="No stock" body="Add inventory to see shelf-life predictions." />
          )}
        </div>
      </Card>

      <Disclaimer />
    </div>
  )
}

function FreshnessBar({ value }) {
  const colour = value > 60 ? '#067647' : value > 30 ? '#B54708' : '#B42318'
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-line">
        <div
          className="h-full rounded-full"
          style={{ width: `${Math.max(3, value)}%`, background: colour }}
        />
      </div>
      <span className="text-xs font-semibold tabular-nums">{value.toFixed(0)}</span>
    </div>
  )
}

function TraceBadge({ status, lotCode }) {
  if (status === 'verified') {
    return (
      <span className="badge bg-clear-soft text-clear" title={lotCode}>
        Verified
      </span>
    )
  }
  if (status === 'chain_broken') {
    return <span className="badge bg-confirmed-soft text-confirmed">Chain broken</span>
  }
  return <span className="badge bg-neutral2-soft text-neutral2">No trace</span>
}

/**
 * Shows every term behind a shelf-life number.
 *
 * The specification's shelf model is a degree-hour curve with published
 * coefficients, not something learned from SATVA's own data. Exposing the
 * inputs keeps that honest and makes the recommendation arguable — which is
 * the point of showing it to a person rather than automating the markdown.
 */
function RationalePanel({ rationale, prediction }) {
  const rows = [
    ['Crop profile', rationale.crop_profile],
    ['Shelf life at reference temp', `${rationale.shelf_life_days_at_reference} days`],
    ['Reference temperature', `${rationale.reference_temperature_c} °C`],
    ['Q10 coefficient', rationale.q10],
    ['Ageing rate multiplier', `${rationale.rate_multiplier}×`],
    ['Ripeness from', rationale.ripeness_source?.replace(/_/g, ' ')],
    ['Temperature from', rationale.temperature_source?.replace(/_/g, ' ')],
    ['Hours of temperature data', rationale.hours_observed],
    ['Days held', rationale.days_held],
    ['Accumulated degree-hours', prediction.accumulated_degree_hours?.toFixed(1)],
  ]

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <div>
        <h4 className="mb-2 text-xs font-bold uppercase tracking-wide text-ink-faint">
          How this was calculated
        </h4>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
          {rows.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-ink-soft">{label}</dt>
              <dd className="font-medium tabular-nums">{value ?? '—'}</dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="space-y-3">
        <div className="rounded-lg border border-line bg-white p-3">
          <p className="text-xs leading-relaxed text-ink-soft">
            <span className="font-semibold text-ink">Model note. </span>
            {rationale.model_note}
          </p>
        </div>
        {rationale.crop_note && (
          <div className="rounded-lg border border-line bg-white p-3">
            <p className="text-xs leading-relaxed text-ink-soft">
              <span className="font-semibold text-ink">About this crop. </span>
              {rationale.crop_note}
            </p>
          </div>
        )}
        <p className="text-xs text-ink-faint">
          Uncertainty widens when ripeness is inferred from age rather than measured, and when
          no temperature tag is fitted. Current interval:{' '}
          <span className="font-semibold">
            ±{(rationale.relative_uncertainty * 100).toFixed(0)}%
          </span>
        </p>
      </div>
    </div>
  )
}
