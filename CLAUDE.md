# put-scanner

Escáner diario para vender puts semanales. Ver README.md.

Tesis del usuario: watchlist de compañías fundamentalmente sólidas, con cash para
sobrevivir una crisis y beta alta para primas más elevadas; vende puts en sus niveles
y rota el portafolio dentro del watchlist. Los chequeos de fundamentales
(`FUNDAMENTALS_FILTER` y siguientes en config.py) implementan esa tesis: no los
aflojes sin que él lo pida. En el resumen diario menciona cash/deuda y FCF de cada
compañía que pase.

## Ejecución diaria (rutina de Claude)

1. `./run_daily.sh` — tarda ~30 min; genera `results.json` y `report.html`.
2. Publicar `report.html` como Artifact actualizando siempre la misma URL
   (la URL está en `ARTIFACT_URL`; si el archivo no existe, publicar uno nuevo y
   guardar la URL ahí).
3. No subir los resultados al repositorio (están en .gitignore). Solo se hace
   commit si se cambia código.

Reglas:
- Es investigación: nunca colocar órdenes ni recomendar comprar o vender.
- Si Yahoo falla o no hay resultados, publicar la página igual con el motivo.
