# Bosowa Project — Handoff

_Last updated: 2026-05-15_

---

## Repos & Deploy

| | Path | Deploy |
|---|---|---|
| **bosowa-agent** | `bosowa-agent/` | push tag `vX.Y.Z` → GitHub Actions → SCP VPS |
| **portal_bosowa** | `portal_bosowa/portal_bosowa/` | `git pull && npx prisma generate && npm run build && pm2 restart bosowa-portal` |

- VPS: `portal.bosowa.co.id`, pm2: `bosowa-portal`, port `3002`
- Downloads: `https://portal.bosowa.co.id/downloads/BosowAgent.exe`
- DB: PostgreSQL, `DATABASE_URL` di `/opt/bosowa/portal_bosowa/.env`

---

## Versi Agent & GitHub Actions

- **Di repo**: `AGENT_VERSION` di `agent/config.py` (nilai lokal).
- **Build otomatis** memakai workflow `.github/workflows/build-release.yml` — **hanya jalan saat push *tag* `v*`** ke GitHub, **bukan** saat `git push` commit biasa ke `main`.
- **Format tag**: `v1.0.7` (huruf `v` + semver). Hindari bentuk salah seperti `v.1.0.7` (titik setelah `v`).
- **Urutan rilis**:
  1. `git push origin main` (atau branch utama)
  2. `git tag v1.0.7` lalu `git push origin v1.0.7`
- **Uji build tanpa deploy ke VPS**: tab Actions → workflow ini → *Run workflow* (`workflow_dispatch`, isi versi mis. `1.0.99`). Job deploy/SCP **hanya** untuk event `push` tag, bukan untuk run manual.
- Jika workflow tidak pernah jalan: cek repo GitHub yang sama, Actions enabled, dan untuk deploy pastikan secrets `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_ENV_FILE` terisi.

## Dua peta lokasi (portal)

| Peta | Halaman | Sumber data |
|------|---------|-------------|
| **Multi-pin** | Dashboard → tampilan **Peta** | `GET /api/devices/locations` + pin hanya jika ada koordinat (sekarang dengan fallback kota/negara via Nominatim bila DB punya kota tapi tanpa lat/lon). |
| **Single-pin** | Detail perangkat → tab Ringkasan → *Lokasi Perangkat* | `GET /api/devices/[mac]/location-logs?limit=1` (fallback koordinat sama). |

Sebelumnya pin multi sering kosong karena IP/geo punya kota/negara tetapi `latitude`/`longitude` null; filter di `dashboard/page.tsx` membutuhkan keduanya terisi.

---

## Deployment Pending

- [ ] Push tag `v1.0.7` (contoh) untuk trigger CI build exe
- [ ] VPS: jalankan migrasi BU color:
  ```sql
  ALTER TABLE "BusinessUnit" ADD COLUMN IF NOT EXISTS "color" TEXT;
  ```
- [ ] VPS: `npx prisma generate && npm run build && pm2 restart bosowa-portal`

---

## Bug / perbaikan terbaru (sesi 15 Mei)

- Ticket count di tray desktop + notifikasi tiket blacklist + audit tanpa LINK + peta: sudah ditangani di kode portal/agent; deploy portal ke VPS agar API peta & lokasi terbaru aktif.

---

## Rencana Fitur (belum dikerjakan)

| # | Fitur | Scope | File utama |
|---|---|---|---|
| 1 | Software whitelist + 3 kategori (BLACKLISTED/WHITELISTED/UNCLASSIFIED) | Medium | Prisma model baru, `/api/settings/software-whitelist`, settings UI, software tab device detail |
| 2 | Hapus link tracking dari audit | Kecil | `agent/core/audit_usage.py`, portal usage history view |
| 3 | Software history report (tanpa link) | Kecil | portal device detail, hapus link fetch |

---

## Arsitektur Penting

- **Update agent**: `/api/agent/version` → agent download ke `AGENT_DIR/update/BosowAgent_new.exe` → PowerShell `Copy-Item` ke `sys.executable` → exit → Task Scheduler restart. Di dev mode (`frozen=False`) replacement di-skip.
- **Lokasi GPS**: Windows `GeoCoordinateWatcher` → fallback ipapi.co/ip-api.com → disimpan di tabel raw SQL `device_location_logs`.
- **Blacklist software**: portal kelola list → heartbeat trigger `checkBlacklistAndCreateTickets()` → buat tiket `HIGH` priority dengan `sourceRef: "blacklist:<name>"`.
- **BU pin color**: `BusinessUnit.color` (nullable hex). Fallback: `buFallbackColor(name)` hash stabil ke palette.
- **Auto-login fix**: `store_user_session(user)` ditambahkan di `_run_auth_flow()` (`main.py`) — sebelumnya login diminta ulang setiap restart.
