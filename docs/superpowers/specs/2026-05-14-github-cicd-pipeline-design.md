# GitHub CI/CD Pipeline untuk BosowAgent — Design Spec
Date: 2026-05-14
Status: Approved

---

## 1. Ringkasan

Source code `bosowa-agent` di-host di GitHub (public repo). Setiap kali developer push Git tag bertanda versi (contoh: `v1.0.3`), GitHub Actions secara otomatis:
1. Build `BosowAgent.exe` di Windows runner menggunakan PyInstaller
2. Patch `AGENT_VERSION` di `config.py` sesuai tag (tidak perlu edit manual)
3. Deploy exe ke VPS via SCP
4. Update env vars di VPS (AGENT_LATEST_VERSION + AGENT_DOWNLOAD_URL)
5. Restart portal via pm2
6. Buat GitHub Release dengan exe sebagai attachment

Mekanisme `UPDATE_AGENT` via Socket.IO di agent dan endpoint `/api/agent/version` di portal tidak berubah.

---

## 2. Data Flow

```
Developer
  git tag v1.0.3
  git push origin v1.0.3
          ↓
GitHub Actions (windows-latest runner)
  1. checkout source
  2. setup Python 3.11
  3. pip install -r requirements.txt
  4. extract versi: "v1.0.3" → "1.0.3"
  5. patch config.py: AGENT_VERSION = '1.0.3'
  6. pyinstaller build/bosowa_agent.spec → build/dist/BosowAgent.exe
  7. SCP exe → VPS:/var/www/downloads/BosowAgent.exe
  8. SSH: update AGENT_LATEST_VERSION=1.0.3 di .env
  9. SSH: update AGENT_DOWNLOAD_URL=https://portal.bosowa.co.id/downloads/BosowAgent.exe di .env
 10. SSH: pm2 restart bosowa-portal
 11. Create GitHub Release (tag + exe asset)
          ↓
VPS nginx serves:
  https://portal.bosowa.co.id/downloads/BosowAgent.exe
          ↓
Agent online terima UPDATE_AGENT command dari dashboard
  → fetch /api/agent/version → versi + download_url
  → download exe baru
  → replace diri sendiri + relaunch
  → versi terbaru aktif ✓
```

---

## 3. File yang Ditambah / Diubah

| File | Jenis | Perubahan |
|------|-------|-----------|
| `.github/workflows/build-release.yml` | Baru | Pipeline CI/CD lengkap |
| `agent/config.py` | Modifikasi minor | AGENT_VERSION tidak perlu diedit manual lagi (CI yang patch) |

Tidak ada perubahan di `portal_bosowa`.

---

## 4. GitHub Secrets (sudah di-setup)

| Secret | Nilai |
|--------|-------|
| `VPS_HOST` | IP / domain VPS |
| `VPS_USER` | Username SSH VPS |
| `VPS_SSH_KEY` | Private key `github_actions` (public key sudah di authorized_keys VPS) |
| `VPS_ENV_FILE` | `/opt/bosowa/portal_bosowa/.env` |

---

## 5. Workflow Detail — `build-release.yml`

### Trigger
```yaml
on:
  push:
    tags:
      - 'v*'
```
Hanya jalan saat tag `v*` di-push. Push biasa ke branch tidak trigger build.

### Jobs
Satu job: `build-and-deploy` di `windows-latest`.

### Steps (urutan)
1. `actions/checkout@v4`
2. `actions/setup-python@v5` — Python 3.11
3. `pip install -r requirements.txt` + `pip install pyinstaller`
4. Extract versi dari tag: `${{ github.ref_name }}` → strip `v` prefix
5. Patch `agent/config.py`: `sed` replace `AGENT_VERSION = '...'` → versi baru
6. `pyinstaller build/bosowa_agent.spec --distpath build/dist --workpath build/work --noconfirm`
7. Verify `build/dist/BosowAgent.exe` exists
8. Install `sshpass` (tidak perlu karena pakai key-based auth) — gunakan `webfactory/ssh-agent` untuk load private key
9. SCP `build/dist/BosowAgent.exe` ke `VPS:/var/www/downloads/BosowAgent.exe`
10. SSH: `sed -i` update `AGENT_LATEST_VERSION` di `.env`
11. SSH: `sed -i` update `AGENT_DOWNLOAD_URL` di `.env`
12. SSH: `pm2 restart bosowa-portal`
13. `softprops/action-gh-release@v2` — buat GitHub Release + upload exe

### Estimasi durasi: 5–8 menit per release

---

## 6. VPS — Nginx Config

Nginx harus serve file statis di `/var/www/downloads/`:

```nginx
location /downloads/ {
    alias /var/www/downloads/;
    add_header Content-Disposition 'attachment';
}
```

Direktori `/var/www/downloads/` dibuat jika belum ada.

---

## 7. Cara Release Versi Baru

```bash
# 1. Pastikan semua commit sudah di main
git push origin main

# 2. Tag versi baru (tidak perlu edit AGENT_VERSION di config.py)
git tag v1.0.3
git push origin v1.0.3

# 3. Tunggu 5-8 menit — GitHub Actions build otomatis
# 4. Cek Actions tab di GitHub untuk progress
# 5. Setelah selesai: portal otomatis tau versi baru,
#    agent online bisa di-update dari dashboard
```

---

## 8. Error Handling

| Kondisi | Perilaku |
|---------|---------|
| PyInstaller gagal | Actions step gagal, workflow berhenti, tidak ada deploy |
| SCP gagal (VPS unreachable) | Step gagal, tidak ada .env update atau pm2 restart |
| pm2 restart gagal | Step gagal, tapi exe sudah terlanjur di VPS — restart manual |
| Tag format salah (tidak `v*`) | Workflow tidak trigger |
| AGENT_LATEST_VERSION tidak match di .env | sed -i idempotent — replace nilai lama dengan baru |

---

## 9. Scope

**Dalam scope:**
- GitHub Actions workflow untuk build + deploy
- Patch AGENT_VERSION otomatis dari tag
- SCP deploy ke VPS
- Update .env + pm2 restart
- GitHub Release artifact

**Di luar scope:**
- Installer Windows (NSIS/Inno Setup) — untuk first install masih manual
- Batch update semua agent sekaligus — masih per-device dari dashboard
- Notifikasi Slack/email saat release — tidak diperlukan
- Rollback otomatis — jika ada masalah, re-tag versi lama
