# Matrices de ejemplo para el cálculo de caminos

Input de un experto con 4 causas y 3 efectos, para probar la página **Caminos** (`/front/fe`).

| Archivo | Forma | Contenido |
|---|---|---|
| `CC.csv` | 4 × 4 | causas × causas |
| `CE.csv` | 4 × 3 | causas × efectos |
| `EE.csv` | 3 × 3 | efectos × efectos |

- La primera fila y la primera columna llevan las etiquetas; la celda de la esquina se ignora.
- Los valores van en [0,1]. La diagonal de CC y EE se fija en 1 al validar.
- `excel_cl/` tiene las mismas matrices como las exporta Excel en configuración chilena:
  separador `;` y decimal con coma.
