# Put Scanner (puts semanales)

Escáner diario gratuito (datos de Yahoo Finance y Nasdaq.com) para encontrar
compañías donde vender puts semanales. Es una herramienta de investigación:
no coloca órdenes.

## Tesis

Armar un **watchlist de compañías fundamentalmente sólidas**, con **cash suficiente para
sobrevivir momentos de crisis** y con **beta alta** para cobrar primas más elevadas. Vendo puts
en los niveles que tengo marcados; si me asignan, me quedo con una compañía que quiero tener,
y el portafolio rota constantemente dentro del watchlist.

Por eso el escáner exige, además de lo técnico (RSI, beta, P/E), que la compañía pase los
chequeos de fundamentales de abajo. Un dato que Yahoo no tenga cuenta como que no pasa.

## Qué filtra

Todo se ajusta en `config.py`.

| Filtro | Valor |
|---|---|
| Universo | Todas las acciones de EE. UU. del screener de Nasdaq con capitalización ≥ $2B |
| Precio | $10 – $70 |
| Liquidez | ≥ $10M negociados al día (promedio de 20 días) |
| Beta (1 año vs SPY) | ≥ 3.0 (perfil IREN/CRWV; calculada con rendimientos diarios) |
| RSI(14) diario | 30 – 50 (Wilder, igual que TradingView; verificado contra cierres de Nasdaq) |
| P/E | positivo y ≤ 60 |
| Fundamentales: supervivencia (todas) | current ratio ≥ 1.2 · deuda respaldada: cash ≥ 50% de la deuda **o** deuda/patrimonio ≤ 1.0 (dueña de sus activos: terreno, energía, equipo) · se financia sola: cash operativo positivo (el capex de expansión no cuenta como quema; si el cash operativo es negativo, caja para ≥ 2 años) |
| Fundamentales: calidad (≥ 2 de 3) | margen operativo positivo · ventas creciendo vs el año anterior · deuda/patrimonio ≤ 1.5 |
| Vencimiento | el primer viernes a 3–10 días (semanal) |
| Earnings | se descarta si hay earnings antes del vencimiento |
| Put | delta 0.12–0.30, OI ≥ 100 o volumen ≥ 10, prima ≥ 0.8% semanal |

## Qué entrega por compañía

- RSI diario, P/E, P/E forward, earnings, sector y descripción de la compañía.
- **Fundamentales**: cash, deuda, current ratio, free cash flow, cash operativo, crecimiento de
  ventas y deuda/patrimonio, con ✓/✗ por cada chequeo de la tesis.
- **Mapa de 20 strikes** alrededor del precio (todos los vencimientos ≤ 60 días) con open
  interest y volumen de calls y puts, y dónde queda el **PISO** (strike debajo del precio con
  más gamma de puts) y el **TECHO** (strike arriba con más gamma de calls).
- Régimen gamma (positivo/negativo) y gamma flip.
- Contratos con más open interest, con más volumen y con volumen inusual (volumen > OI).
- Puts semanales que pasan los filtros, con prima, rendimiento y si el strike queda debajo del piso.

Después del cierre la prima es el **último precio del día** (los market makers retiran las
cotizaciones); durante el mercado es el punto medio bid/ask.

## Uso

```bash
./run_daily.sh          # instala dependencias, escanea y genera report.html
```

Archivos: `results.json`, `report.html`, `daily_put_report.md`, `ticker_summary.csv`,
`put_candidates_full.csv`, `daily_put_report.csv`.

Solo tu lista de `TICKERS` (más rápido): `UNIVERSE = "list"` en `config.py`.

## Advertencias

- Los niveles de gamma son estimaciones con datos públicos: no se sabe qué lado (dealer o
  cliente) tiene cada contrato. No son soportes garantizados.
- El rendimiento anualizado es una normalización, no un retorno esperado.
