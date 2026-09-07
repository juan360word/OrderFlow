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
  /**
   * Cómo encaja la foto en su hueco.
   *
   * `contain` (el valor por defecto) muestra la foto entera: se reduce hasta
   * caber y deja aire alrededor si su proporción no coincide con la del hueco.
   * `cover` rellena el hueco recortando lo que sobra.
   *
   * El defecto es `contain` porque las fotos de un catálogo las suben personas
   * distintas, en vertical, cuadradas y apaisadas, y `cover` recortaba
   * justamente el producto: una foto vertical perdía la mitad de arriba y la
   * mitad de abajo. Enseñar el producto completo importa más que llenar cada
   * píxel del recuadro.
   */
  fit?: 'contain' | 'cover'
  /** Aire entre la foto y el borde del hueco, en píxeles. */
  inset?: number
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
  fit = 'contain',
  inset = 0,
}: Props) {
  const [broken, setBroken] = useState(false)
  const showImage = url !== null && !broken

  return (
    <div
      className={className}
      style={{
        ...style,
        ...(showImage
          ? {
              // Con una foto dentro, el rayado del marcador sobra: en
              // `contain` se vería a través del aire que rodea la imagen.
              backgroundImage: 'none',
              overflow: 'hidden',
              padding: inset,
              // Centrado en los dos ejes, que es donde `contain` deja el
              // sobrante. Sin esto, una foto apaisada se pegaría arriba.
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }
          : {}),
      }}
    >
      {showImage ? (
        <img
          src={url}
          alt={alt}
          onError={() => setBroken(true)}
          style={{
            // La caja siempre es el hueco entero y `object-fit` decide qué
            // hace la foto dentro. Dejar que la imagen conservara su tamaño
            // natural parecía más pulcro, pero una foto más pequeña que el
            // hueco quedaba perdida en una esquina.
            width: '100%',
            height: '100%',
            objectFit: fit,
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
