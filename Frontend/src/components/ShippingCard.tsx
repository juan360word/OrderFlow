import { useState } from 'react'
import { MapPin, Copy, Check, Phone, StickyNote } from 'lucide-react'
import { countryName } from '../lib/countries'
import type { ShippingAddress } from '../lib/schemas'

type Props = {
  address: ShippingAddress | null
  /** El admin es quien despacha: para él la dirección es la acción, no un dato. */
  emphasis?: boolean
}

/** La dirección en las líneas en que se escribiría en una guía de envío. */
function addressLines(address: ShippingAddress): string[] {
  const locality = [address.city, address.region].filter(Boolean).join(', ')
  const country = [address.postal_code, countryName(address.country)]
    .filter(Boolean)
    .join(' · ')
  return [address.line1, address.line2, locality, country].filter(
    (line): line is string => !!line,
  )
}

export function ShippingCard({ address, emphasis = false }: Props) {
  const [copied, setCopied] = useState(false)

  if (!address) {
    return (
      <div
        style={{
          borderTop: '1px solid var(--line)',
          paddingTop: 16,
          fontSize: 13,
          color: 'var(--ink2)',
        }}
      >
        Este pedido se hizo antes de que se pidiera la dirección de envío.
      </div>
    )
  }

  const lines = addressLines(address)

  async function copyToClipboard() {
    if (!address) return
    const text = [address.recipient_name, address.phone, ...lines, address.notes ?? '']
      .filter(Boolean)
      .join('\n')
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Sin permiso de portapapeles (o sin HTTPS): la dirección sigue a la
      // vista para copiarla a mano, así que no hay nada que avisar.
    }
  }

  return (
    <div
      style={{
        background: 'var(--sunk)',
        boxShadow: 'var(--sh-in)',
        borderRadius: 16,
        padding: 18,
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        ...(emphasis ? { outline: '1.5px solid var(--accent)', outlineOffset: -1.5 } : {}),
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <MapPin size={15} color={emphasis ? 'var(--accent)' : 'var(--ink2)'} />
          <span className="section-label">{emphasis ? 'Enviar a' : 'Dirección de envío'}</span>
        </div>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={copyToClipboard}
          aria-label="Copiar la dirección"
        >
          {copied ? <Check size={13} color="var(--ok)" /> : <Copy size={13} />}
          {copied ? 'Copiado' : 'Copiar'}
        </button>
      </div>

      <div>
        <p style={{ fontWeight: 700, fontSize: 15, color: 'var(--ink)' }}>
          {address.recipient_name}
        </p>
        <p
          className="mono"
          style={{
            fontSize: 13,
            color: 'var(--ink2)',
            marginTop: 2,
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <Phone size={12} />
          {address.phone}
        </p>
      </div>

      <address style={{ fontStyle: 'normal', fontSize: 13.5, lineHeight: 1.7, color: 'var(--ink)' }}>
        {lines.map((line) => (
          <div key={line}>{line}</div>
        ))}
      </address>

      {address.notes && (
        <div
          style={{
            display: 'flex',
            gap: 8,
            alignItems: 'flex-start',
            fontSize: 12.5,
            color: 'var(--ink2)',
            borderTop: '1px solid var(--line)',
            paddingTop: 10,
          }}
        >
          <StickyNote size={13} style={{ flexShrink: 0, marginTop: 2 }} />
          <span>{address.notes}</span>
        </div>
      )}
    </div>
  )
}
