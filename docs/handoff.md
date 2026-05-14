# Bosowa Project — Handoff

_Last updated: 2026-05-15 (malam — user off to sleep)_

---

## Status singkat (malam 15 Mei)

| Area | Status |
|------|--------|
| **Login loop / auto-login** | **Selesai** — penyebab: `_restrict_file` di `auth/token_store.py` memanggil `icacls` tanpa grant user yang menjalankan agent → `tokens.enc` tidak bisa dibaca (`Permission denied`) → overlay login berulang. Fix: tambahkan `USERNAME:F` pada `icacls`. Rilis agent baru + hapus sekali `tokens.enc` lama di PC yang terlanjur kunci ACL. |
| **Peta dashboard / pin lokasi** | **Belum berfungsi** di lingkungan user — lanjut debug: pastikan portal ter-deploy (`server.ts` `maybeLogLocation`, heartbeat/register kirim geo), agent kirim lokasi, DB `device_location_logs`, `GET /api/devices/locations`, komponen peta di `app/dashboard/page.tsx`. |
| **Unduh exe dari browser** | Gunakan **`/api/downloads/agent-exe`** (sesi login admin) bila URL `/downloads/BosowAgent.exe` bermasalah; proxy dari `AGENT_DOWNLOAD_URL`. |

---

## Repos & Deploy

| | Path | Deploy |
|---|---|---|
| **bosowa-agent** | `bosowa-agent/` | push tag `vX.Y.Z` → GitHub Actions → SCP VPS |
| **portal_bosowa** | `portal_bosowa/portal_bosowa/` | `git pull && npx prisma generate && npm run build && pm2 restart bosowa-portal --update-env` |

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

- [ ] Push tag semver baru (`v1.1.x` dst.) bila ada perubahan agent — CI hanya dari **tag** `v*`.
- [ ] VPS portal: `git pull`, `npm run build`, **`pm2 restart bosowa-portal --update-env`** (wajib agar `AGENT_LATEST_VERSION` dari `.env` terbaca).
- [ ] VPS: migrasi BU color jika belum:
  ```sql
  ALTER TABLE "BusinessUnit" ADD COLUMN IF NOT EXISTS "color" TEXT;
  ```

---

## Bug / perbaikan terbaru (sesi 15 Mei)

- **Login berulang:** fix `token_store._restrict_file` + rilis exe baru (lihat tabel status di atas).
- **UPDATE_AGENT / verifikasi versi:** portal + agent terbaru — event `restarting` + `target_version`, UI verifikasi vs heartbeat; unduh resmi `/api/downloads/agent-exe`.
- **Peta:** user konfirmasi **masih belum jalan** setelah deploy — **TODO** berikutnya: end-to-end lokasi (agent → socket → Prisma → API → Leaflet), cek data di `device_location_logs` dan response `/api/devices/locations`.

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
