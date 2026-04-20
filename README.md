<p align="center">
  <h1 align="center">📡 MeshStation 📡</h1>
  <p align="center"><i>Meshtastic & MeshCore SDR Analyzer & Desktop GUI</i></p>
  <br>
  <p align="center">
    <a href="https://ko-fi.com/IronGiu"><img src="https://img.shields.io/badge/Ko--fi-FF5E5B?logo=ko-fi&logoColor=white&label=Donate" alt="Ko-fi"></a>&nbsp;
    <a href="https://irongiu.com"><img src="https://img.shields.io/badge/%F0%9F%8C%90-Website-blue" alt="Website"></a>&nbsp;
    <a href="https://discord.gg/rwV5q5FPCm"><img src="https://img.shields.io/discord/719925738781802528?style=flat&logo=discord&label=Discord" alt="Discord"></a>&nbsp;
    <a href="https://x.com/irongiu"><img src="https://img.shields.io/badge/X-%23000000.svg?logo=X&logoColor=white&label=IronGiu" alt="X"></a>&nbsp;
    <a href="https://www.instagram.com/irongiu_official/"><img src="https://img.shields.io/badge/Instagram-%23E4405F.svg?logo=Instagram&logoColor=white" alt="Instagram"></a>&nbsp;
    <a href="https://twitch.tv/irongiu"><img src="https://img.shields.io/badge/Twitch-%239146FF.svg?logo=Twitch&logoColor=white" alt="Twitch"></a>&nbsp;
  </p>
  <p align="center">
    <a href="https://meshstationapi.irongiu.com/download?os=win-64"><img src="https://img.shields.io/badge/DOWNLOAD-Windows_x86__64-0078D4?style=for-the-badge&logo=windows&logoColor=white" alt="Windows x86_64"></a>&nbsp;
    <a href="https://meshstationapi.irongiu.com/download?os=linux-64"><img src="https://img.shields.io/badge/DOWNLOAD-Linux_x86__64-FCC624?style=for-the-badge&logo=linux&logoColor=white" alt="Linux x86_64"></a>&nbsp;
    <a href="https://meshstationapi.irongiu.com/download?os=linux-arm64"><img src="https://img.shields.io/badge/DOWNLOAD-Linux_ARM64-FCC624?style=for-the-badge&logo=linux&logoColor=white" alt="Linux ARM64"></a>&nbsp;
    <a href="https://meshstationapi.irongiu.com/download?os=macos-arm"><img src="https://img.shields.io/badge/DOWNLOAD-macOS_Apple_Silicon-black?style=for-the-badge&logo=apple&logoColor=white" alt="macOS Apple Silicon"></a>&nbsp;
    <a href="https://meshstationapi.irongiu.com/download?os=macos-64"><img src="https://img.shields.io/badge/DOWNLOAD-macOS_Intel_x86__64-black?style=for-the-badge&logo=apple&logoColor=white" alt="macOS Intel x86_64"></a>
  </p>
  <p align="center">
    <a href="https://github.com/IronGiu/MeshStation/wiki">WIKI / DOCUMENTATION (EN/IT)</a>
  </p>
</p>

<a id="english"></a>
<p align="center">
  <a href="#english">🇬🇧 English Readme</a> / <a href="#italian">🇮🇹 Readme Italiano</a>
</p>

## 🇬🇧 English

**MeshStation** is an open‑source Meshtastic and MeshCore SDR Analyzer and Desktop GUI.
It decodes Meshtastic and MeshCore packets using any SDR, displays nodes on a map,
shows chat messages, node database/list, mesh network overview and quality, raw console data, and will support TX in the future.

In fact this is a real-time mesh network observatory powered by **Software Defined Radio (SDR)**.
It passively listens to the RF spectrum and decodes live Meshtastic or MeshCore traffic directly from the air — **no mesh device required**.

This project is designed for **network discovery, coverage analysis, network density, network quality, research, and real-world mapping of Meshtastic nodes**, using only a simple RTL-SDR receiver.

<img width="192" height="108" alt="MeSt-present-eng" src="https://github.com/user-attachments/assets/d19d2f25-fbf4-41a4-89c3-7211b99dbbdd" />
<img width="192" height="104" alt="MeSt-screen-1-eng" src="https://github.com/user-attachments/assets/4bfef775-d369-4f8b-86b6-f63fe59bb6d3" />
<img width="192" height="104" alt="MeSt-screen-2-eng" src="https://github.com/user-attachments/assets/20ef9da6-0ead-463d-80ed-9f20f50f3f6d" />
<img width="192" height="104" alt="MeSt-screen-2-light-eng" src="https://github.com/user-attachments/assets/22f56ae1-c9a4-467d-985a-da9e8dd88ca4" />
<img width="192" height="104" alt="MeSt-screen-3-eng" src="https://github.com/user-attachments/assets/b39f0f99-c407-426d-951d-52971d3ca900" />
<img width="192" height="103" alt="MeSt-screen-4-eng" src="https://github.com/user-attachments/assets/1d41d524-4ff2-4b04-8e16-317e83938bec" />

---

### 🚀 What MeshStation Can Do

MeshStation works in **real time**, receiving data straight from the ether.  
This enables use cases that are currently hard or impossible with standard Meshtastic setups.

#### 🗺️ Coverage & Reachability
- Detect **Meshtastic nodes active in a specific geographic area**
- Understand **whether you are already covered by nearby nodes**
- Evaluate network density **before buying a compatible Meshtastic device**
- Analyze signal reach and RF presence over time
- Monitor network quality and performance
- **Scan all modem presets at once (LongFast, MediumFast etc.)**
- **Scan all the MeshTastic frequencies in the world**

#### 🕸️ MeshCore Ecosystem Support
- **Repeaters & Companions**: Full visualization of MeshCore's structured, repeater-centric hierarchy.
- **Role-based Markers**: Instant visual identification of Repeaters (Triangle), Companions (Circle), and Room Servers (Square).
- **Multi-hop Pathing**: Real-time dashed-line visualization of the repeater chain a packet traveled through.
- **Regional Presets**: One-click configuration for USA/Canada (910.525 MHz) and Legacy Wide (915.8 MHz) deployments.

#### 🧠 Network Mapping & Research
- Map the **real Meshtastic network as it actually exists**
- Build a **database of nodes**, including:
  - Node IDs
  - Metadata
  - Routing and network behavior
- Create **real-world maps based on live RF feeds**, not self-reported data
- Perform long-term studies on:
  - Network growth
  - Stability
  - Topology evolution

#### 📢 Public Message Monitoring
- Read **unencrypted / public Meshtastic messages**
- Stay informed about **what is happening on the network**
- Observe real-time community activity and traffic patterns
*(private/encrypted messages are not decrypted)* 

### 🔐 Monitoring channels
- Read even encrypted channels (with the correct key and exact name)
- Check if the channel is active before adding it (since you have limited channels in the official firmware)
- Monitor an unlimited number of channels by adding them to the "Chat Messages" section with the dedicated button.

#### 🧪 Experimentation & Testing
- Test Meshtastic behavior using **only an SDR as receiver**
- Analyze protocol behavior, airtime usage, and packet flow
- Use MeshStation as a **passive test bench** for RF and mesh experiments

---

### 🔮 Future Plans

If adequate **financial and community support** is reached, MeshStation aims to evolve further:

- 📡 **Transmission support** (TX), not only reception
- 🔁 Active interaction with the Meshtastic network
- 🧭 Advanced live network visualization
- 🌐 Shared or federated node maps
- 📊 Historical analytics dashboards
- 👩‍💻 Integrated API service for receiving/sending both in headless and interface
- 🤖 Network automation and messaging with n8n support

---

### 🧰 Requirements

- RTL-SDR compatible device
- Supported SDR drivers
- A system capable of real-time SDR processing

**Are you on Windows 11?** Check out the [Windows 11 Setup Guide](WINDOWS_SETUP.md) for step-by-step instructions on hardware, drivers, and startup.

**Visit the wiki if you're having trouble with drivers or to learn how to "install" the app:** [https://github.com/IronGiu/MeshStation/wiki](https://github.com/IronGiu/MeshStation/wiki)

---

### ⚠️ Disclaimer

MeshStation is intended for **research, educational, and experimental purposes**.  
Always comply with **local laws and radio regulations**.

---

### ❤️ Support the Project

If you find this project useful or exciting:
- Star the repository ⭐
- Share it with the Meshtastic and SDR communities
- Consider supporting future development

---

<p align="center">
  <a href="#english">🇬🇧 English Readme</a> / <a href="#italian">🇮🇹 Readme Italiano</a>
</p>

## 🇮🇹 Italiano

**MeshStation** è un analizzatore SDR Meshtastic e MeshCore open source e un'interfaccia grafica desktop.
Decodifica i pacchetti Meshtastic e MeshCore utilizzando qualsiasi SDR, visualizza i nodi su una mappa,
mostra messaggi di chat, database/lista nodi, Panoramica rete mesh e qualità, dati grezzi della console e supporterà la trasmissione in futuro.

Infatti questo è un osservatorio della rete mesh in tempo reale basato su **Software Defined Radio (SDR)**.
Ascolta passivamente lo spettro RF e decodifica il traffico Meshtastic o MeshCore **direttamente dall’etere**, senza bisogno di alcun dispositivo mesh.

Il progetto è pensato per **scoperta della rete, analisi di copertura, traffico della rete, qualità della rete, studio e mappatura reale dei nodi Meshtastic**, utilizzando solo un semplice ricevitore RTL-SDR.

<img width="192" height="108" alt="MeSt-present-eng" src="https://github.com/user-attachments/assets/d19d2f25-fbf4-41a4-89c3-7211b99dbbdd" />
<img width="192" height="104" alt="MeSt-screen-1-eng" src="https://github.com/user-attachments/assets/4bfef775-d369-4f8b-86b6-f63fe59bb6d3" />
<img width="192" height="104" alt="MeSt-screen-2-eng" src="https://github.com/user-attachments/assets/20ef9da6-0ead-463d-80ed-9f20f50f3f6d" />
<img width="192" height="104" alt="MeSt-screen-2-light-eng" src="https://github.com/user-attachments/assets/22f56ae1-c9a4-467d-985a-da9e8dd88ca4" />
<img width="192" height="104" alt="MeSt-screen-3-eng" src="https://github.com/user-attachments/assets/b39f0f99-c407-426d-951d-52971d3ca900" />
<img width="192" height="103" alt="MeSt-screen-4-eng" src="https://github.com/user-attachments/assets/1d41d524-4ff2-4b04-8e16-317e83938bec" />

---

### 🚀 Cosa può fare MeshStation

MeshStation lavora **in tempo reale**, ricevendo dati direttamente dall’etere.  
Questo permette casi d’uso difficili o impossibili con le configurazioni Meshtastic tradizionali.

#### 🗺️ Copertura & Raggiungibilità
- Rilevare **nodi Meshtastic attivi in una zona specifica**
- Capire **se si è già coperti da nodi vicini**
- Valutare la presenza della rete **prima di acquistare un device compatibile**
- Analizzare la copertura RF nel tempo
- Monitorare la qualità e le performance della rete
- **Scansionare contemporaneamente tutti i modem-preset (LongFast, MediumFast ecc)**
- **Scansionare tutte le frequenze MeshTastic del mondo**

#### 🕸️ Supporto per l'Ecosistema MeshCore
- **Ripetitori e Companion**: Visualizzazione completa della gerarchia strutturata e incentrata sui ripetitori di MeshCore.
- **Marcatori basati sui ruoli**: Identificazione visiva istantanea di Ripetitori (Triangolo), Companion (Cerchio) e Server di Stanza (Quadrato).
- **Percorsi Multi-hop**: Visualizzazione in tempo reale (linea tratteggiata) della catena di ripetitori attraversata da un pacchetto.
- **Preset Regionali**: Configurazione con un clic per i paesi USA/Canada (910.525 MHz) e Legacy Wide (915.8 MHz).

#### 🧠 Mappatura & Studio della Rete
- Mappare la **rete Meshtastic reale**, così com’è davvero
- Creare un **database dei nodi**, includendo:
  - ID dei nodi
  - Metadati
  - Comportamento di rete e routing
- Generare **mappe basate su feed RF reali e live**
- Condurre studi su:
  - Crescita della rete
  - Stabilità
  - Evoluzione della topologia

#### 📢 Lettura dei Messaggi Pubblici
- Leggere **messaggi Meshtastic pubblici/non cifrati**
- Rimanere informati su **ciò che accade nella rete**
- Osservare attività e traffico in tempo reale  

### 🔐 Monitoraggio canali
- Leggi i canali anche criptati (con la giusta chiave e il nome esatto)
- Guarda se il canale è attivo prima di aggiungerlo (visto che hai canali limitati nel firmware ufficiale)
- Monitora una quantità di canali illimitati aggiungendoli nella sezione "Messaggi Chat" con l'apposito tasto.

#### 🧪 Test & Sperimentazione
- Testare Meshtastic usando **solo un SDR come ricevitore**
- Analizzare comportamento del protocollo e uso dell’etere
- Utilizzare MeshStation come **banco di prova passivo** per esperimenti RF e mesh

---

### 🔮 Sviluppi Futuri

Con un adeguato **supporto economico e della community**, MeshStation potrà evolversi ulteriormente:

- 📡 **Supporto alla trasmissione (TX)**, non solo ricezione
- 🔁 Interazione attiva con la rete Meshtastic
- 🧭 Visualizzazioni avanzate della rete in tempo reale
- 🌐 Mappe dei nodi condivise o federate
- 📊 Analisi storiche e dashboard
- 👩‍💻 Servizio API integrato per ricezione/invio sia in headless che interfaccia
- 🤖 Automazioni di rete e messaggi con supporto n8n

---

### 🧰 Requisiti

- Dispositivo compatibile RTL-SDR
- Driver SDR supportati
- Sistema in grado di gestire SDR in tempo reale

**Sei su Windows 11?** Consulta la [Guida all'installazione per Windows 11](WINDOWS_SETUP.md) per istruzioni dettagliate su hardware, driver e avvio.

**Vai nella wiki se hai problemi con driver o per sapere come "installare" l'app:** [https://github.com/IronGiu/MeshStation/wiki](https://github.com/IronGiu/MeshStation/wiki)

---

### ⚠️ Disclaimer

MeshStation è pensato per **scopi di studio, ricerca ed educativi**.  
Rispettare sempre le **leggi locali e le normative radio**.

---

### ❤️ Supporta il Progetto

Se il progetto ti sembra utile o interessante:
- Metti una stella alla repository ⭐
- Condividilo con la community Meshtastic e SDR
- Valuta di supportarne lo sviluppo futuro

