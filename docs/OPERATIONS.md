# Cafe POS - Operations & Administration Manual

This guide covers setup, daily routines, backup/recovery, application updates, and printer troubleshooting for **Cafe POS**.

---

## Table of Contents

1. [Architecture & System Requirements](#1-architecture--system-requirements)
2. [First-Time Setup](#2-first-time-setup)
3. [Creating the First Owner](#3-creating-the-first-owner)
4. [Daily Open / Close Routine](#4-daily-open--close-routine)
5. [Database Backup & Restore](#5-database-backup--restore)
6. [Updating the Application](#6-updating-the-application)
7. [Thermal Printer Troubleshooting Checklist](#7-thermal-printer-troubleshooting-checklist)

---

## 1. Architecture & System Requirements

- **Runtime**: Python 3.10, 3.11, or 3.12.
- **Database**: SQLite 3 with WAL (Write-Ahead Logging) enabled.
- **Static Assets**: Served via WhiteNoise with aggressive caching and offline service worker compatibility.
- **WSGI Production Server**: Waitress (multi-threaded, production-ready, Windows and Linux friendly).
- **Network Scope**: Designed for offline-first, local network operation on `0.0.0.0:8000`.

---

## 2. First-Time Setup

### Step 1: Clone or Extract the Project
```bash
cd C:\Users\User\Desktop\cafe_pos_django_v2
```

### Step 2: Create and Activate Virtual Environment
**Windows (PowerShell / Command Prompt):**
```powershell
python -m venv .venv
.venv\Scripts\activate
```

**Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Step 3: Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 4: Configure Environment Variables
Copy `.env.example` to `.env` if not already present:
```bash
# Windows
copy .env.example .env

# Linux / macOS
cp .env.example .env
```

Ensure `.env` contains:
```ini
DEBUG=False
SECRET_KEY=your-random-generated-secret-key-here
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost,0.0.0.0
DJANGO_HTTPS=False
```
*(To generate a fresh secure key: `python -c "import secrets; print(secrets.token_urlsafe(50))"`)*

### Step 5: Run Database Migrations
```bash
python manage.py migrate
```

### Step 6: Collect Static Assets
```bash
python manage.py collectstatic --noinput
```

### Step 7: Compile Language Catalogs (English / Tamil)
```bash
python manage.py compilemessages
```

---

## 3. Creating the First Owner

Cafe POS enforces role-based access control with two primary roles:
- **Owner**: Full access to financial reports, profit & loss, settings, stock purchases, staff management, wastage adjustments, and audit logs.
- **Cashier**: Billing, shift operations, cash drawer reconciliation, customer khata lookups, and order holding.

To create the initial Owner administrator:
```bash
python manage.py createsuperuser
```
Follow the prompts to enter:
1. Username (e.g. `owner`)
2. Email (optional, can press Enter)
3. Password and password confirmation

Log in at `http://127.0.0.1:8000/login/`. Superusers automatically receive Owner privileges. Additional staff members can be added anytime through the **Staff Management** page (`/staff/`).

---

## 4. Daily Open / Close Routine

### Morning / Shift Start Routine
1. **Start Server**:
   - Double-click `run_server.bat` (Windows) or execute `./run_server.sh` (Linux).
2. **Login**:
   - Open browser at `http://localhost:8000/` and sign in.
3. **Open Shift (`/shift/open/`)**:
   - Count the physical cash in the drawer (the opening float/petty cash).
   - Enter the **Opening Cash** amount (e.g., `₹500.00`).
   - Click **Open Shift**.
   *(Note: Billing is disabled until an active shift is open to prevent untracked cash drift).*

### Operating Throughout the Day
- **Billing (`/billing/`)**:
  - Add items via quick touch tiles or keyboard shortcut codes (`Ctrl + code` or type code and press Enter).
  - Select payment method: **Cash**, **UPI**, **Card**, **Khata (Credit)**, or **Split**.
  - On tender, change due is computed instantly.
  - Held orders can be parked with `/billing/hold/` and recalled when customers return.
- **Petty Cash Expenses (`/expenses/add/`)**:
  - For store supplies (milk, lemons, cleaning items), record expenses.
  - Check **Paid from drawer** if cash was removed from the active shift till.
- **Wastage Logging (`/wastage/add/`)**:
  - Record spilled, expired, or returned finished goods or raw materials.

### Evening / Shift & Day Close Routine
1. **Close Shift (`/day-close/`)**:
   - Physically count all currency notes and coins in the cash drawer.
   - Enter the **Counted Cash in Drawer**.
   - The system displays:
     - Opening Cash float
     - Cash sales collected
     - Drawer expenses deducted
     - Expected closing cash vs. Actual cash counted
     - Any variance (surplus or shortage)
   - Add explanation notes if variance exists, then submit **Close Shift**.
2. **Review Daily Summary (`/daily-summary/`)**:
   - Owners can review total gross sales, bill count, payment breakdowns, expenses, net profit, top items, and outstanding dues.
   - Click **Copy summary** or click **Share via WhatsApp** to instantly send the day's financial snapshot to the owner's mobile phone via WhatsApp.

---

## 5. Database Backup & Restore

The application uses SQLite's official online backup API (`sqlite3_backup`), making it completely safe to back up while bills are actively being entered.

### Creating Backups
Backups can be triggered in three ways:
1. **Settings Page UI**:
   - Navigate to **Settings** (`/settings/`).
   - Click the **Backup now** button.
2. **Command Line / Scripts**:
   ```bash
   python manage.py backup_db
   ```
3. **Custom Backup Directory (USB Drive)**:
   - On the **Settings** page, specify a custom path in **Backup Directory** (e.g. `D:\CafeBackups` or `/media/usb/backups`).
   - If the drive is disconnected, the system shows an amber warning pill and falls back safely to the local `backups/` directory.

### Backup Verification & Retention
- Every backup file is verified with `PRAGMA integrity_check` before being committed.
- The system automatically retains the **30 newest backups** and cleans up older copies.

### Restoring from a Backup
To restore the database from a backup file:
```bash
# 1. Inspect existing backups
dir backups

# 2. Run restore command (always creates an automatic pre-restore safety copy first)
python manage.py restore_db backups/db_20261013_120000.sqlite3 --confirm
```
*(Omitting `--confirm` runs in dry-run mode and verifies the file integrity without modifying live data).*

### Automating Backups
- **Windows Task Scheduler**:
  Create a daily scheduled task running:
  `C:\Users\User\Desktop\cafe_pos_django_v2\.venv\Scripts\python.exe manage.py backup_db`
- **Linux / macOS Cron**:
  ```cron
  0 23 * * * cd /path/to/cafe_pos && .venv/bin/python manage.py backup_db > /dev/null 2>&1
  ```

---

## 6. Updating the Application

When receiving new code updates or bug fixes:

1. **Stop the Running Server**:
   - Press `Ctrl + C` in the Waitress server window.
2. **Backup Current Database**:
   ```bash
   python manage.py backup_db
   ```
3. **Pull Updates**:
   ```bash
   git pull origin main
   ```
4. **Update Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
5. **Apply Migrations**:
   ```bash
   python manage.py migrate
   ```
6. **Collect Static Files**:
   ```bash
   python manage.py collectstatic --noinput
   ```
7. **Compile Messages**:
   ```bash
   python manage.py compilemessages
   ```
8. **Restart Server**:
   - Execute `run_server.bat` or `./run_server.sh`.

---

## 7. Thermal Printer Troubleshooting Checklist

The system supports 80mm and 58mm ESC/POS thermal printers connected over USB, Ethernet/Wi-Fi (Network), or RS-232 Serial COM ports.

### Configuration (`/settings/`)
- **Printer Enabled**: Toggle ON.
- **Connection Type**: Choose `USB`, `Network`, `Serial`, or `Virtual/Dummy`.
- **Address**:
  - USB: Vendor ID & Product ID in hexadecimal (e.g., `04b8:0e15`).
  - Network: IP address and port (e.g., `192.168.1.100:9100`).
  - Serial: COM port identifier (e.g., `COM3` on Windows or `/dev/ttyUSB0` on Linux).
- **Paper Width**: `80mm` (~48 characters/line) or `58mm` (~32 characters/line).
- **Auto-Cut**: Sends `GS V` cut command after receipt.
- **Drawer Kick**: Sends `ESC p` electrical pulse to open RJ11 connected till drawer.
- **Kitchen Counter Slip**: Prints compact item-only ticket for kitchen staff.

---

### Step-by-Step Troubleshooting

| Issue | Cause | Solution |
| :--- | :--- | :--- |
| **"Printer timeout" or nothing prints** | Wrong USB IDs or unassigned driver | 1. Find USB IDs on Windows: Open PowerShell and run `Get-PnpDevice -PresentOnly \| Where-Object { $_.FriendlyName -like "*POS*" -or $_.Class -eq "USB" }`.<br>2. Format as `VID:PID` (e.g., `04b8:0e15`).<br>3. In `/settings/`, click **Test Print** to diagnose. |
| **Network printer not responding** | IP changed or port blocked | 1. Ping printer IP: `ping 192.168.1.100`.<br>2. Ensure printer port is default `9100`.<br>3. Set static DHCP IP reservation on router for printer MAC. |
| **Garbled characters or question marks** | Codepage mismatch (Tamil/Unicode) | ESC/POS standard single-byte thermal printers do not support raw Unicode UTF-8 Tamil. The printing engine automatically transliterates standard cafe phrases (`மசாலா சாய்` $\to$ `Masala Chai`) and strips incompatible glyphs to preserve clean text. |
| **Printer fails but bill is saved** | Automatic Fallback Activated | The billing page is async-safe: if the thermal printer fails within 2.5 seconds, a warning toast appears and the browser print dialog (`window.print()`) automatically opens as a zero-downtime fallback. |
| **Paper cuts too early / cuts text** | Insufficient trailing linefeeds | The receipt builder appends 3 line feeds prior to `GS V` cut. Ensure printer paper feed tension roller is clean. |
| **Cash drawer does not kick** | Cable plugged into wrong port or wrong voltage | Cash drawer must be plugged into the printer's RJ11 port (not the PC). Check if drawer solenoid is 12V or 24V compatible with your printer model. |
| **Windows USB access error (`libusb0 not found`)** | Windows driver binding | If using raw USB mode, Windows requires standard WinUSB / libusb driver binding for the device (using the free Zadig utility). Alternatively, configure as a Network printer or COM port. |

