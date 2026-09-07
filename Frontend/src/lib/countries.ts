/**
 * Los países que la tienda envía, y cómo se llaman en pantalla.
 *
 * Lo que se guarda y viaja por la API es el código ISO 3166-1 alpha-2; el
 * nombre existe solo para leerlo. Separarlos evita el problema clásico de
 * guardar "Colombia" en una fila y "colombia" en la siguiente.
 */
export const COUNTRIES: { code: string; name: string }[] = [
  { code: 'CO', name: 'Colombia' },
  { code: 'MX', name: 'México' },
  { code: 'AR', name: 'Argentina' },
  { code: 'CL', name: 'Chile' },
  { code: 'PE', name: 'Perú' },
  { code: 'EC', name: 'Ecuador' },
  { code: 'UY', name: 'Uruguay' },
  { code: 'PA', name: 'Panamá' },
  { code: 'CR', name: 'Costa Rica' },
  { code: 'ES', name: 'España' },
  { code: 'US', name: 'Estados Unidos' },
]

const BY_CODE = new Map(COUNTRIES.map((c) => [c.code, c.name]))

/** El nombre del país, o el propio código si es uno que la lista no cubre. */
export function countryName(code: string): string {
  return BY_CODE.get(code.toUpperCase()) ?? code.toUpperCase()
}
