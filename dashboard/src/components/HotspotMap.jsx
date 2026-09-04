/**
 * MapLibre GL hotspot map over OpenStreetMap tiles.
 *
 * Two modes, and the difference is a product rule rather than a preference:
 *
 * - **Public** (`officerView = false`) renders ward *areas* at the ward's own
 *   representative point, sized by reading count. It never receives a cluster
 *   centroid, because the public API does not return one — the boundary is
 *   enforced in the API, and this component simply has nothing precise to draw.
 * - **Officer** renders cluster centroids with their true radius, including
 *   clusters withheld from the public map.
 *
 * Non-negotiable rule 4: public maps show areas, not named vendors.
 */

import { useEffect, useMemo, useRef } from 'react'
import maplibregl from 'maplibre-gl'

// Raster OSM style, defined inline rather than fetched from a style server:
// one less external dependency for a demo that may run on venue wifi.
const OSM_STYLE = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
      maxzoom: 19,
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
}

const SEVERITY_COLOUR = {
  high: '#B42318',
  elevated: '#B54708',
  advisory: '#5B7FBE',
}

// Palakkad district, the pilot area named in the specification.
const DEFAULT_CENTRE = [76.6548, 10.7757]

export default function HotspotMap({
  clusters = [],
  wards = [],
  selectedId,
  onSelect,
  officerView = false,
}) {
  const container = useRef(null)
  const map = useRef(null)
  const markers = useRef([])

  const points = useMemo(() => {
    if (officerView) {
      return clusters
        .filter((c) => c.centroid)
        .map((c) => ({
          id: c.id,
          lat: c.centroid.latitude,
          lon: c.centroid.longitude,
          severity: c.severity,
          count: c.member_count,
          devices: c.independent_device_count,
          label: c.ward_name ?? c.ward_code ?? 'Unmapped',
          published: c.is_publishable,
          suppression: c.suppression_reason,
          demo: c.contains_demo_data,
        }))
    }
    return wards.map((w) => ({
      id: w.ward_code,
      lat: w.representative_point.latitude,
      lon: w.representative_point.longitude,
      severity: w.highest_severity,
      count: w.confirmed_reading_count,
      label: w.ward_name ?? w.ward_code,
      published: true,
      demo: w.contains_demo_data,
    }))
  }, [clusters, wards, officerView])

  useEffect(() => {
    if (map.current || !container.current) return
    map.current = new maplibregl.Map({
      container: container.current,
      style: OSM_STYLE,
      center: DEFAULT_CENTRE,
      zoom: 9,
      attributionControl: { compact: true },
    })
    map.current.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    return () => {
      map.current?.remove()
      map.current = null
    }
  }, [])

  useEffect(() => {
    if (!map.current) return

    markers.current.forEach((m) => m.remove())
    markers.current = []

    points.forEach((point) => {
      const colour = SEVERITY_COLOUR[point.severity] ?? SEVERITY_COLOUR.advisory
      // Area scales with reading count, clamped so one busy ward does not
      // swallow the map and a single reading is still visible.
      const size = Math.max(26, Math.min(66, 20 + Math.sqrt(point.count) * 9))
      const isSelected = point.id === selectedId

      const el = document.createElement('button')
      el.type = 'button'
      el.setAttribute('aria-label', `${point.label}: ${point.count} confirmed readings`)
      el.style.cssText = `
        width:${size}px;height:${size}px;border-radius:9999px;cursor:pointer;
        background:${colour}${point.published ? '33' : '1A'};
        border:${isSelected ? 3 : 2}px ${point.published ? 'solid' : 'dashed'} ${colour};
        display:flex;align-items:center;justify-content:center;
        color:${colour};font-weight:800;font-size:${size > 40 ? 13 : 11}px;
        font-family:Inter,system-ui,sans-serif;
        box-shadow:${isSelected ? `0 0 0 4px ${colour}22` : 'none'};
        transition:box-shadow .15s ease;
      `
      el.textContent = String(point.count)

      const popup = new maplibregl.Popup({ offset: size / 2 + 6, closeButton: false }).setHTML(`
        <div style="font-family:Inter,system-ui,sans-serif;padding:2px 4px;min-width:170px">
          <div style="font-weight:700;font-size:13px;margin-bottom:2px">${escapeHtml(point.label)}</div>
          <div style="font-size:12px;color:#475467">
            ${point.count} confirmed reading${point.count === 1 ? '' : 's'}
            ${point.devices != null ? `<br/>${point.devices} independent device${point.devices === 1 ? '' : 's'}` : ''}
          </div>
          ${
            point.published
              ? ''
              : `<div style="margin-top:6px;font-size:11px;color:#B54708;line-height:1.35">
                   Withheld from the public map${point.suppression ? `: ${escapeHtml(point.suppression.replace(/_/g, ' '))}` : ''}
                 </div>`
          }
          ${
            point.demo
              ? `<div style="margin-top:6px;font-size:10px;font-weight:700;text-transform:uppercase;
                            letter-spacing:.04em;color:#475467">Demo data</div>`
              : ''
          }
        </div>
      `)

      if (onSelect) el.addEventListener('click', () => onSelect(point.id))

      const marker = new maplibregl.Marker({ element: el })
        .setLngLat([point.lon, point.lat])
        .setPopup(popup)
        .addTo(map.current)
      markers.current.push(marker)
    })

    if (points.length > 0) {
      const bounds = new maplibregl.LngLatBounds()
      points.forEach((p) => bounds.extend([p.lon, p.lat]))
      map.current.fitBounds(bounds, { padding: 70, maxZoom: 12, duration: 600 })
    }
  }, [points, selectedId, onSelect])

  return (
    <div className="relative h-full w-full">
      <div ref={container} className="h-full w-full" />
      <div className="pointer-events-none absolute bottom-3 left-3 rounded-lg bg-white/95 px-3 py-2 text-[11px] shadow-card">
        <p className="mb-1 font-bold uppercase tracking-wide text-ink-faint">Severity</p>
        <div className="space-y-1">
          {Object.entries(SEVERITY_COLOUR).map(([key, colour]) => (
            <div key={key} className="flex items-center gap-2">
              <span
                className="h-2.5 w-2.5 rounded-full"
                style={{ background: colour }}
              />
              <span className="capitalize text-ink-soft">{key}</span>
            </div>
          ))}
        </div>
        {officerView && (
          <p className="mt-2 max-w-[190px] leading-snug text-ink-faint">
            Dashed outline = withheld from the public map for lack of corroboration.
          </p>
        )}
      </div>
    </div>
  )
}

function escapeHtml(value) {
  return String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  )
}
