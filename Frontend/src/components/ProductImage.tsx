import { useState } from 'react'
import type { CSSProperties } from 'react'
import { ImageOff } from 'lucide-react'

type Props = {
  /** Ruta que sirve el backend, o null si el producto no tiene foto. */
  url: string | null
  alt: string
  /** Texto del marcador cuando no hay foto. */
  placeholder: string
  className?: string
  style?: CSSProperties
  rounded?: number
}

/**
 * La foto de un producto, o el marcador rayado cuando no hay ninguna.
 *
 * Vive en un componente porque el catálogo, el detalle y el carrito muestran
 * exactamente lo mismo, y porque el caso de error importa: si la imagen se
 * borra desde otra pestaña, el `<img>` queda roto y el navegador dibuja el
 * icono de imagen partida. `onError` lo devuelve al marcador.
 */
export function ProductImage({
  url,
  alt,
  placeholder,
  className,
  style,
  rounded,
}: Props) {
  const [broken, setBroken] = useState(false)
  const showImage = url !== null && !broken

  return (
    <div
      className={className}
      style={{
        ...style,
        ...(showImage
          ? { backgroundImage: 'none', padding: 0, overflow: 'hidden' }
          : {}),
      }}
    >
      {showImage ? (
        <img
          src={url}
          alt={alt}
          onError={() => setBroken(true)}
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            display: 'block',
            borderRadius: rounded ?? 0,
          }}
        />
      ) : (
        <span className="product-thumb-label">
          {broken ? <ImageOff size={12} /> : null} {placeholder}
        </span>
      )}
    </div>
  )
}
