# Local Drop & Share 📡

Condivisione istantanea di file o intere cartelle sulla propria rete locale Wi-Fi / LAN tramite server temporaneo e QR Code, senza passare dal cloud (**100% Locale / Zero Cloud**).

Il progetto è strutturato come **progetto duale**:
1. **Applicazione Desktop Nativa per Linux**: confezionata in un comodo **AppImage** standalone con GUI dark (PyQt6) e Drag & Drop.
2. **Componente Aggiuntivo per Home Assistant (Add-on)**: pacchetto ultra-leggero (< 30 MB RAM, 0% CPU idle) integrato nella barra laterale via **Ingress**, installabile con 1 clic incollando il link del repository GitHub su Home Assistant OS / Supervised.

---

## 🏠 Utilizzo con Home Assistant (Add-on)

Il repository è configurato per funzionare direttamente come **Repository di Add-on per Home Assistant**.

### Installazione su Home Assistant
1. In Home Assistant vai su **Impostazioni** &rarr; **Componenti aggiuntivi** &rarr; **Raccolta di componenti aggiuntivi** (in basso a destra).
2. Fai clic sui **tre puntini** in alto a destra &rarr; **Repository**.
3. Incolla l'URL del tuo repository GitHub:
   ```text
   https://github.com/TUO_UTENTE/TUO_REPO
   ```
4. Fai clic su **Aggiungi**, ricarica la pagina e troverai l'add-on **Local Drop & Share**.
5. Fai clic su **Installa**, abilita **Mostra nella barra laterale** e premi **Avvia**.

### Perché è perfetto per Home Assistant
- **Consumo Risorse Minimo**: Solo ~20-30 MB di RAM e 0% CPU in idle, non rallenta Raspberry Pi o mini-PC.
- **Accesso a `/share` e `/media`**: Condividi backup, video di sorveglianza o registrazioni già presenti su Home Assistant.
- **Interfaccia Web Ingress**: Drag & drop di file dal tuo browser direttamente nella schermata di Home Assistant.
- **Web UI & Download LAN Diretto (porta 5001)**: Aprendo `http://<IP_HA>:5001` da smartphone o PC sul Wi-Fi, puoi sia scaricare sia **caricare foto, video o documenti direttamente dal cellulare** per condividerli sulla rete locale senza dover fare il login su Home Assistant.

Per tutti i dettagli vedi la [Guida Add-on](local-drop-share/DOCS.md).

---

## 💻 Applicazione Desktop Linux (AppImage)

### Avvio Rapido
Puoi avviare direttamente il pacchetto AppImage precompilato:
```bash
./Local_Drop_Share-x86_64.AppImage
```

Oppure eseguire direttamente il codice sorgente Python:
```bash
pip install -r requirements.txt
python3 local_drop_share.py
```

### Compilazione dell'AppImage
Lo script automatizzato `build_appimage.sh` compila il sorgente con PyInstaller e genera il binario SquashFS AppImage:
```bash
./build_appimage.sh
```

---

## 📁 Struttura del Repository

```text
├── repository.yaml             # Definizione del repository di add-on per Home Assistant
├── local-drop-share/           # Directory del componente aggiuntivo Home Assistant
│   ├── config.yaml             # Configurazione add-on (Ingress, porte, permessi /share)
│   ├── Dockerfile              # Immagine Alpine multi-architettura super-leggera
│   ├── build.yaml              # Supporto multi-arch (aarch64, amd64, armv7, armhf)
│   ├── run.sh                  # Script di avvio per Home Assistant
│   ├── server.py               # Backend Python leggero (Ingress Web UI + download LAN)
│   ├── DOCS.md                 # Documentazione d'uso in Home Assistant
│   ├── CHANGELOG.md            # Cronologia versioni
│   ├── icon.png                # Icona dell'add-on nello store
│   └── logo.png                # Logo dell'add-on
├── local_drop_share.py         # Applicazione desktop Linux con GUI PyQt6
├── build_appimage.sh           # Script di build per pacchettizzare l'AppImage
├── generate_icon.py            # Generatore icone vettoriali/grafiche
├── requirements.txt            # Dipendenze Python desktop
└── Local_Drop_Share-x86_64.AppImage # Binario AppImage autonomo già pronto
```
