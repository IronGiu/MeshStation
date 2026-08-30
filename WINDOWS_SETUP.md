# 🪟 Windows 11 Setup Guide for MeshStation

Follow these steps to get MeshStation running on your Windows 11 machine.

## 1. Prerequisites
### Hardware
- A compatible **RTL-SDR dongle** (e.g., RTL-SDR Blog V3/V4, Noelec, HackRF).
- An appropriate antenna for the frequency you intend to monitor (e.g., 868MHz or 915MHz).

### Software
- **Python 3.10 or 3.11**: [Download from python.org](https://www.python.org/downloads/windows/). Ensure you check **"Add Python to PATH"** during installation.
- **Git**: [Download from git-scm.com](https://git-scm.com/download/win) (if you are cloning the repository).

---

## 2. Installation Steps

### Step A: Clone or Download
Clone the repository or download the source code as a ZIP and extract it to a folder (e.g., `C:\MeshStation`).

### Step B: Install Python Dependencies
Open **PowerShell** or **Command Prompt** in the project folder and run:
```powershell
pip install -r requirements.txt
```

### Step C: Build the SDR Radio Engine
MeshStation uses a high-performance GNU Radio-based engine. On Windows, you need to build the portable runtime:
1. Navigate to the folder: `engine\os\win_x86_64\`
2. Double-click on `auto-engine-builder.bat`.
3. Wait for the process to complete. It will download the necessary components and create a `runtime` folder.

---

## 3. SDR Driver Setup (Zadig)
Windows often installs a default "DVB-T" driver for RTL-SDR dongles, which prevents them from being used as general-purpose SDRs.

1. Plug in your RTL-SDR dongle.
2. Download and run **Zadig**: [https://zadig.akeo.ie/](https://zadig.akeo.ie/)
3. Go to `Options` -> `List All Devices`.
4. Select your SDR from the dropdown (often called `Bulk-In, Interface (Interface 0)` or `RTL2838UHIDIR`).
5. Ensure the target driver (right side) is **WinUSB**.
6. Click **Replace Driver** (or **Install Driver**).

---

## 4. Starting MeshStation
Go back to the root project folder and run:
```powershell
python MeshStation.py
```

---

## 5. Basic Usage
1. **Connect**: Click the **Connect** button in the top menu.
2. **Choose Ecosystem**: In the Connection Settings, use the **Network Ecosystem** toggle to select **Meshtastic** or **MeshCore**.
3. **Configure Settings**:
   - **Meshtastic**: Select your Region (e.g., US, EU_868) and Modem Preset.
   - **MeshCore**: Select a Regional Preset (e.g., USA/Canada) or choose "Custom" to set your own frequency and SF.
4. **Start**: Click **Connect** inside the settings panel. You should see the console log starting the engine and, eventually, nodes appearing on the map!

---

## 🛠️ Troubleshooting
- **No device found**: Ensure you performed the Zadig driver replacement. Try a different USB port.
- **Engine won't start**: Re-run the `auto-engine-builder.bat` script. Ensure no other SDR software (like SDR# or CubicSDR) is using the dongle.
- **Visual Glitches**: If the map looks strange, try running with the `--nogpu` flag: `python MeshStation.py --nogpu`.
