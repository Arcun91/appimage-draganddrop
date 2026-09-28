# Local Drop & Share — Home Assistant Add-on

Condividi file e cartelle all'istante sulla tua rete Wi-Fi/LAN locale direttamente da **Home Assistant**, generando un codice QR scansionabile con lo smartphone per il download P2P diretto ad alta velocità, senza passare da alcun servizio cloud (**100% Locale / Zero Cloud**).

---

## ⚡ Caratteristiche e Risparmio Risorse

- **Estremamente Parsimonioso**: Sviluppato specificamente per non appesantire Raspberry Pi o mini-PC a basse risorse (consumo di memoria **< 30 MB di RAM** e **0% CPU in idle**).
- **Integrazione Ingress**: L'interfaccia di gestione è integrata direttamente nella barra laterale di Home Assistant.
- **Supporto Storage Home Assistant**: Condividi file presenti in `/share` e `/media` (es. backup, clip di videosorveglianza, log, foto) con un solo clic.
- **Upload Drag-and-Drop**: Trascina direttamente qualsiasi file dalla finestra del browser nel dashboard di Home Assistant; il file viene trasmesso in streaming direttamente su disco senza occupare RAM.
- **Download & Upload LAN Diretto (Porta 5001)**: Aprendo `http://<IP_HOME_ASSISTANT>:5001` da qualsiasi smartphone, tablet o PC connesso al Wi-Fi, puoi caricare file dal telefono (foto, video, documenti) oppure scaricare i file attivi, senza bisogno di effettuare il login su Home Assistant.
- **Supporto Range Requests**: Permette di riprendere download interrotti su browser mobili (Safari iOS e Chrome Android).

---

## 🛠️ Come Installare su Home Assistant

1. Accedi a Home Assistant come amministratore.
2. Vai su **Impostazioni** (`Settings`) &rarr; **Componenti aggiuntivi** (`Add-ons`) &rarr; **Raccolta di componenti aggiuntivi** (`Add-on Store`).
3. Fai clic sui **tre puntini in alto a destra** e seleziona **Repository**.
4. Incolla l'URL del tuo repository GitHub:
   ```text
   https://github.com/TUO_UTENTE/TUO_REPO
   ```
5. Fai clic su **Aggiungi** (`Add`) e attendi il caricamento.
6. Nella lista dei componenti aggiuntivi comparirà la card **Local Drop & Share**.
7. Fai clic sulla card e premi **Installa** (`Install`).
8. Attiva l'opzione **Mostra nella barra laterale** (`Show in sidebar`).
9. Premi **Avvia** (`Start`).

---

## ⚙️ Opzioni di Configurazione

Nel tab **Configurazione** dell'add-on puoi personalizzare:

```yaml
lan_port: 5001
lan_ip_override: ""
auto_clean_hours: 24
```

| Parametro | Descrizione | Default |
| :--- | :--- | :--- |
| `lan_port` | La porta TCP aperta sulla rete locale LAN per la Web UI completa e il download dei file da smartphone/PC. | `5001` |
| `lan_ip_override` | *(Opzionale)* Forza un IP locale specifico se Home Assistant è in una VLAN o configurazione di rete complessa. Se lasciato vuoto, l'IP viene rilevato automaticamente. | `""` |
| `auto_clean_hours` | Ore dopo le quali i file temporanei caricati tramite browser vengono ripuliti automaticamente per non riempire lo storage. | `24` |

---

## 📱 Utilizzo

1. Apri **Drop & Share** dalla barra laterale di Home Assistant.
2. **Carica o scegli un file**:
   - Trascina un file dal tuo PC nella schermata.
   - Oppure seleziona il tab *Seleziona da Home Assistant* per condividere file già presenti sul server (es. backup `.tar`).
3. Inquadra il **QR Code** con la fotocamera dello smartphone (connesso allo stesso Wi-Fi).
4. Il download del file inizierà istantaneamente!
5. Quando hai terminato, premi **Ferma Condivisione** per interrompere il server locale e ripulire i file temporanei.
