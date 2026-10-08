# TTLINK

FastAPI que emite links tipo hotel (`/o/{token}`) para abrir una cerradura TTLock vía gateway (`/v3/lock/unlock`) y, opcionalmente, envía una eKey con `remoteEnable=1` (`/v3/key/send`).

- Config: copiar `.env.example` a `.env` y llenar credenciales de open platform + usuario TTLock (password en MD5).
- Correr: `.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000`; admin en `/admin`.
- Tests: `.venv\Scripts\python.exe -m pytest` (cliente TTLock mockeado).
- La apertura remota la ejecuta el token de la cuenta dueña, no la eKey del huésped.
- Sin probar contra cuenta/cerradura real todavía. `TTLOCK_API_BASE` por defecto `https://euapi.ttlock.com`; la doc usa `https://api.sciener.com`.
- Producción (Docker): `docker compose up -d --build` con `.env` al lado; 1 solo worker (SQLite + token OAuth en memoria). Sin puertos publicados: se expone por un reverse proxy (Caddy) en la red `ttlink_ttlink_net`. SQLite en el volumen `ttlink_data` (`/data/ttlink.db`).
- Respaldo: `backup_ttlink.sh` (copia SQLite consistente, cifrada con gpg; requiere `.backup_passphrase`, que no se versiona).
- Lector RFID MR-11U (HID propietario, VID D808/PID 0201; protocolo en github.com/TechElectro/lector-mr11u): `/admin` lo lee por WebHID (Chrome/Edge) y escribe el UID en decimal invertido (little-endian), igual que `uid_teclado.py`. Con ese formato el servidor debe correr con `TTLOCK_CARD_REVERSED=true`. El lector se comparte con otra app: el panel lo abre solo al pulsar "Leer tarjeta" y lo cierra apenas lee una (o al cancelar, a los 20 s, al ocultar la pestaña o cerrar sesión).
