# TTLINK

FastAPI que emite links tipo hotel (`/o/{token}`) para abrir una cerradura TTLock vía gateway (`/v3/lock/unlock`) y, opcionalmente, envía una eKey con `remoteEnable=1` (`/v3/key/send`).

- Config: copiar `.env.example` a `.env` y llenar credenciales de open platform + usuario TTLock (password en MD5).
- Correr: `.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000`; admin en `/admin`.
- Tests: `.venv\Scripts\python.exe -m pytest` (cliente TTLock mockeado).
- La apertura remota la ejecuta el token de la cuenta dueña, no la eKey del huésped.
- Sin probar contra cuenta/cerradura real todavía. `TTLOCK_API_BASE` por defecto `https://euapi.ttlock.com`; la doc usa `https://api.sciener.com`.
