# GitHub CI/CD Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push a Git tag → GitHub Actions builds BosowAgent.exe on Windows runner → SCP to VPS → portal env updated → agent bisa auto-update dari dashboard.

**Architecture:** Single GitHub Actions workflow (`build-release.yml`) triggered on `v*` tags. Windows runner installs Python 3.11 + PyInstaller, patches `AGENT_VERSION` dari tag, build exe, deploy ke VPS via `appleboy/scp-action`, update `.env` via `appleboy/ssh-action`, restart pm2. GitHub Release dibuat otomatis sebagai dokumentasi.

**Tech Stack:** GitHub Actions, PyInstaller 6.5.0, Python 3.11, `appleboy/scp-action@v0.1.7`, `appleboy/ssh-action@v1`, `softprops/action-gh-release@v2`, nginx static files, pm2.

---

## File Structure

| File | Jenis | Keterangan |
|------|-------|-----------|
| `assets/PORTAL.png` | Baru (copy) | Logo branding — harus ada di repo agar CI bisa bundle ke exe |
| `build/bosowa_agent.spec` | Modifikasi | Fix hardcoded ROOT path + fix path PORTAL.png |
| `.github/workflows/build-release.yml` | Baru | Pipeline CI/CD |

---

### Task 1: Fix bosowa_agent.spec — portable paths

**Masalah:** `ROOT` di spec hardcoded ke `C:\Users\adipu\...`. Di GitHub Actions runner path berbeda (`D:\a\bosowa-agent\bosowa-agent\`). Dan `PORTAL.png` di-bundle dari luar repo (`ROOT.parent`). Keduanya akan gagal di CI.

**Solusi:** ROOT dihitung dari posisi file spec itu sendiri. PORTAL.png dipindah ke `assets/PORTAL.png` dalam repo (sudah dicopy di langkah sebelum plan ini ditulis).

**Files:**
- Modify: `build/bosowa_agent.spec:11`
- Modify: `build/bosowa_agent.spec:21`
- Existing: `assets/PORTAL.png` (sudah ada)

- [ ] **Step 1: Buka `build/bosowa_agent.spec` dan ganti baris ROOT**

Ganti:
```python
ROOT = Path(r'C:\Users\adipu\Documents\WebApp\portal_bosowa\bosowa-agent').absolute()
AGENT_SRC = ROOT / 'agent'
sys.path.insert(0, str(AGENT_SRC))
```

Dengan:
```python
# Portable: works on any machine and on GitHub Actions runner
ROOT = Path(__file__).resolve().parent.parent
AGENT_SRC = ROOT / 'agent'
sys.path.insert(0, str(AGENT_SRC))
```

- [ ] **Step 2: Ganti path PORTAL.png di bagian `datas`**

Ganti:
```python
        (str(ROOT.parent / 'PORTAL.png'), 'assets'),
```

Dengan:
```python
        (str(ROOT / 'assets' / 'PORTAL.png'), 'assets'),
```

- [ ] **Step 3: Rebuild lokal untuk verifikasi**

Jalankan di `C:\Users\adipu\Documents\WebApp\portal_bosowa\bosowa-agent`:
```
py -3.11 -m PyInstaller build/bosowa_agent.spec --distpath build/dist --workpath build/work --noconfirm
```

Expected output (baris terakhir):
```
INFO: Building EXE from EXE-00.toc completed successfully.
```

Verifikasi file ada:
```
dir build\dist\BosowAgent.exe
```
Expected: file ~50 MB ada, tidak ada error.

- [ ] **Step 4: Commit**

```bash
git add assets/PORTAL.png build/bosowa_agent.spec
git commit -m "fix: portable PyInstaller spec — dynamic ROOT, PORTAL.png in-repo"
```

---

### Task 2: Create GitHub repo dan push source

**Files:** tidak ada file baru — hanya git operations.

Prerequisite: kamu harus sudah punya akun GitHub dan sudah login di browser.

- [ ] **Step 1: Buat repo baru di GitHub**

Buka https://github.com/new dan isi:
- Repository name: `bosowa-agent`
- Visibility: **Public**
- Initialize: **jangan centang apapun** (repo lokal sudah ada commit)
- Klik "Create repository"

- [ ] **Step 2: Tambah remote origin**

Jalankan di `C:\Users\adipu\Documents\WebApp\portal_bosowa\bosowa-agent` (ganti `USERNAME` dengan username GitHub kamu):
```bash
git remote add origin https://github.com/USERNAME/bosowa-agent.git
```

- [ ] **Step 3: Push semua commit**

```bash
git push -u origin main
```

Expected: semua commit terpush, output seperti:
```
Branch 'main' set up to track remote branch 'main' from 'origin'.
```

- [ ] **Step 4: Verifikasi di browser**

Buka `https://github.com/USERNAME/bosowa-agent` — pastikan semua file ada (agent/, build/, assets/, dll.).

---

### Task 3: Buat GitHub Actions workflow

**Files:**
- Create: `.github/workflows/build-release.yml`

- [ ] **Step 1: Buat direktori dan file workflow**

Buat file `.github/workflows/build-release.yml` dengan isi berikut (ganti `portal.bosowa.co.id` dengan domain/IP VPS kamu di bagian `DOWNLOAD_URL` jika `VPS_HOST` berisi IP, bukan domain):

```yaml
name: Build and Release BosowAgent

on:
  push:
    tags:
      - 'v*'

permissions:
  contents: write

jobs:
  build-and-deploy:
    runs-on: windows-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install pyinstaller==6.5.0

      - name: Extract version from tag
        id: version
        shell: bash
        run: echo "VERSION=${GITHUB_REF_NAME#v}" >> $GITHUB_OUTPUT

      - name: Patch AGENT_VERSION in config.py
        shell: pwsh
        run: |
          $v = "${{ steps.version.outputs.VERSION }}"
          $c = Get-Content agent/config.py -Raw
          $c = $c -replace "AGENT_VERSION = '[^']*'", "AGENT_VERSION = '$v'"
          Set-Content agent/config.py $c -Encoding UTF8

      - name: Build with PyInstaller
        run: python -m PyInstaller build/bosowa_agent.spec --distpath build/dist --workpath build/work --noconfirm

      - name: Verify build output
        shell: bash
        run: |
          test -f build/dist/BosowAgent.exe
          echo "Build OK: $(wc -c < build/dist/BosowAgent.exe) bytes"

      - name: Deploy exe to VPS
        uses: appleboy/scp-action@v0.1.7
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          source: build/dist/BosowAgent.exe
          target: /var/www/downloads/
          strip_components: 2

      - name: Update VPS env and restart portal
        uses: appleboy/ssh-action@v1
        env:
          VERSION: ${{ steps.version.outputs.VERSION }}
          DOWNLOAD_URL: https://${{ secrets.VPS_HOST }}/downloads/BosowAgent.exe
          ENV_FILE: ${{ secrets.VPS_ENV_FILE }}
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          envs: VERSION,DOWNLOAD_URL,ENV_FILE
          script: |
            grep -q "^AGENT_LATEST_VERSION=" "$ENV_FILE" \
              && sed -i "s/^AGENT_LATEST_VERSION=.*/AGENT_LATEST_VERSION=$VERSION/" "$ENV_FILE" \
              || echo "AGENT_LATEST_VERSION=$VERSION" >> "$ENV_FILE"
            grep -q "^AGENT_DOWNLOAD_URL=" "$ENV_FILE" \
              && sed -i "s|^AGENT_DOWNLOAD_URL=.*|AGENT_DOWNLOAD_URL=$DOWNLOAD_URL|" "$ENV_FILE" \
              || echo "AGENT_DOWNLOAD_URL=$DOWNLOAD_URL" >> "$ENV_FILE"
            pm2 restart bosowa-portal

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          files: build/dist/BosowAgent.exe
          generate_release_notes: true
```

- [ ] **Step 2: Commit dan push ke main**

```bash
git add .github/workflows/build-release.yml
git commit -m "ci: add GitHub Actions workflow — build exe on tag push"
git push origin main
```

- [ ] **Step 3: Verifikasi di GitHub**

Buka `https://github.com/USERNAME/bosowa-agent/actions` — harusnya muncul tab "Actions" tapi belum ada run (karena belum ada tag). Kalau tab Actions muncul, workflow sudah terdeteksi dengan benar.

---

### Task 4: VPS — Setup nginx untuk serve /downloads/

**Files:** tidak ada file di repo — ini konfigurasi di VPS via SSH.

Jalankan semua perintah berikut via SSH ke VPS (`ssh user@VPS_HOST`):

- [ ] **Step 1: Buat direktori downloads**

```bash
mkdir -p /var/www/downloads
chmod 755 /var/www/downloads
```

- [ ] **Step 2: Cari file nginx config yang aktif**

```bash
cat /etc/nginx/sites-enabled/default 2>/dev/null || ls /etc/nginx/sites-enabled/
```

Cari file yang berisi `server_name portal.bosowa.co.id` atau `listen 443`.

- [ ] **Step 3: Tambah location block untuk /downloads/**

Buka file nginx config yang ditemukan (biasanya `/etc/nginx/sites-available/bosowa` atau `/etc/nginx/sites-enabled/default`):

```bash
nano /etc/nginx/sites-available/bosowa
```

Tambahkan di dalam blok `server { ... }`, sebelum `location / {`:

```nginx
    location /downloads/ {
        alias /var/www/downloads/;
        add_header Content-Disposition 'attachment';
    }
```

- [ ] **Step 4: Test dan reload nginx**

```bash
nginx -t
```
Expected: `nginx: configuration file /etc/nginx/nginx.conf test is successful`

```bash
systemctl reload nginx
```

- [ ] **Step 5: Verifikasi endpoint aktif**

```bash
curl -I https://portal.bosowa.co.id/downloads/test.txt 2>/dev/null | head -5
```

Expected: `HTTP/2 404` (404 berarti nginx merespon — file belum ada tapi path sudah dikenali). Bukan 502 atau connection refused.

---

### Task 5: First release — tag dan verify pipeline

- [ ] **Step 1: Pastikan semua perubahan sudah di-push ke main**

```bash
git log --oneline origin/main | head -5
```

Semua commit dari Task 1–3 harus muncul.

- [ ] **Step 2: Push tag v1.0.2 untuk trigger pipeline**

```bash
git tag v1.0.2
git push origin v1.0.2
```

- [ ] **Step 3: Monitor progress di GitHub Actions**

Buka `https://github.com/USERNAME/bosowa-agent/actions` — dalam ~30 detik muncul run baru "Build and Release BosowAgent". Klik untuk lihat detail tiap step. Total durasi estimasi 5–8 menit.

Jika ada step yang fail (merah), klik step tersebut untuk lihat log error.

- [ ] **Step 4: Verifikasi exe tersedia di VPS**

Setelah pipeline hijau semua, jalankan di lokal atau di VPS:

```bash
curl -I https://portal.bosowa.co.id/downloads/BosowAgent.exe
```

Expected:
```
HTTP/2 200
content-type: application/octet-stream
content-length: <~50MB dalam bytes>
```

- [ ] **Step 5: Verifikasi /api/agent/version sudah ter-update**

```bash
curl https://portal.bosowa.co.id/api/agent/version
```

Expected:
```json
{"version":"1.0.2","download_url":"https://portal.bosowa.co.id/downloads/BosowAgent.exe","required":false}
```

- [ ] **Step 6: Verifikasi GitHub Release terbuat**

Buka `https://github.com/USERNAME/bosowa-agent/releases` — harus ada release `v1.0.2` dengan `BosowAgent.exe` sebagai attachment.

---

## Cara Release Versi Berikutnya

Setelah setup selesai, cara release versi baru selamanya hanya:

```bash
# Di terminal bosowa-agent (tidak perlu edit AGENT_VERSION di config.py)
git tag v1.0.3
git push origin v1.0.3
# Tunggu 5-8 menit → selesai
```
