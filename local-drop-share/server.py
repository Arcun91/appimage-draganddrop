#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local Drop & Share - Home Assistant Add-on Backend
==================================================
Server web multi-thread ultra-leggero per Home Assistant.
Supporta:
- Porta 5001 (LAN): Web UI completa & endpoint di download accessibile da smartphone e PC
  senza dover effettuare il login su Home Assistant. Permette l'upload da cellulare (foto, video, file)
  e la successiva condivisione istantanea sulla LAN con QR Code.
- Porta 8099 (Ingress): Web UI integrata nella barra laterale di Home Assistant.
- Bassissimo consumo di risorse: ~20-30 MB RAM, 0% CPU in idle, streaming a blocchi da 64 KB.
"""

import os
import sys
import io
import json
import time
import socket
import urllib.parse
import urllib.request
import mimetypes
import threading
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import qrcode
import qrcode.image.svg


# ==============================================================================
# STATO GLOBALE IN MEMORIA (THREAD-SAFE SINGLETON)
# ==============================================================================

class ShareState:
    """Stato globale condiviso in memoria tra i thread Ingress e LAN."""
    def __init__(self):
        self.lock = threading.Lock()
        self.active_file_path: str | None = None
        self.display_name: str | None = None
        self.file_size: int = 0
        self.is_temp: bool = False
        self.is_folder_zip: bool = False
        self.lan_port: int = 5001
        self.ingress_port: int = 8099
        self.lan_ip: str = "127.0.0.1"
        self.logs: list[dict] = []  # max 50 log per risparmiare memoria

    def add_log(self, message: str, level: str = "info"):
        with self.lock:
            entry = {
                "time": datetime.now().strftime("%H:%M:%S"),
                "msg": message,
                "level": level
            }
            self.logs.append(entry)
            if len(self.logs) > 50:
                self.logs.pop(0)

    def set_file(self, path: str, display_name: str, is_temp: bool = False, is_zip: bool = False):
        with self.lock:
            self.active_file_path = path
            self.display_name = display_name
            self.file_size = os.path.getsize(path) if os.path.exists(path) else 0
            self.is_temp = is_temp
            self.is_folder_zip = is_zip
        self.add_log(f"File pronto per la condivisione: {display_name} ({self._format_size(self.file_size)})", "info")

    def stop_sharing(self):
        with self.lock:
            if self.is_temp and self.active_file_path and os.path.exists(self.active_file_path):
                try:
                    os.remove(self.active_file_path)
                except Exception:
                    pass
            self.active_file_path = None
            self.display_name = None
            self.file_size = 0
            self.is_temp = False
            self.is_folder_zip = False
        self.add_log("Condivisione fermata. Nessun file attivo.", "info")

    def get_status(self) -> dict:
        with self.lock:
            is_active = bool(self.active_file_path and os.path.exists(self.active_file_path))
            direct_download_url = ""
            portal_url = f"http://{self.lan_ip}:{self.lan_port}/"
            qr_svg = ""
            if is_active:
                encoded_name = urllib.parse.quote(self.display_name)
                direct_download_url = f"http://{self.lan_ip}:{self.lan_port}/{encoded_name}"
                # Il QR Code punta direttamente al download del file per gli smartphone
                qr_svg = self._generate_qr_svg(direct_download_url)

            return {
                "active": is_active,
                "file_name": self.display_name or "",
                "file_size": self.file_size,
                "file_size_formatted": self._format_size(self.file_size),
                "is_folder": self.is_folder_zip,
                "download_url": direct_download_url,
                "portal_url": portal_url,
                "qr_svg": qr_svg,
                "lan_ip": self.lan_ip,
                "lan_port": self.lan_port,
                "logs": list(self.logs)
            }

    def _generate_qr_svg(self, url: str) -> str:
        try:
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=2,
                image_factory=qrcode.image.svg.SvgPathImage
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image()
            buf = io.BytesIO()
            img.save(buf)
            return buf.getvalue().decode('utf-8')
        except Exception as e:
            return f"<svg><text>Errore generazione QR: {e}</text></svg>"

    def _format_size(self, size_bytes: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"


STATE = ShareState()


# ==============================================================================
# INDIVIDUAZIONE IP DI HOME ASSISTANT
# ==============================================================================

def detect_host_ip(override_ip: str = "") -> str:
    """Rileva l'IP LAN dell'host Home Assistant."""
    if override_ip and override_ip.strip():
        return override_ip.strip()

    # Metodo 1: Home Assistant Supervisor API
    token = os.environ.get("SUPERVISOR_TOKEN")
    if token:
        try:
            req = urllib.request.Request(
                "http://supervisor/network/info",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                interfaces = data.get("data", {}).get("interfaces", [])
                for iface in interfaces:
                    ipv4 = iface.get("ipv4", {})
                    addrs = ipv4.get("address", [])
                    for addr in addrs:
                        ip = addr.split('/')[0]
                        if not ip.startswith('127.') and not ip.startswith('172.30.'):
                            return ip
        except Exception:
            pass

    # Metodo 2: Socket UDP verso gateway (senza traffico esterno)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = '127.0.0.1'
    finally:
        s.close()
    return ip


def load_options() -> dict:
    """Legge le impostazioni dell'utente da /data/options.json."""
    options = {
        "lan_port": 5001,
        "download_port": 5001,
        "lan_ip_override": "",
        "auto_clean_hours": 24
    }
    opt_file = "/data/options.json"
    if os.path.exists(opt_file):
        try:
            with open(opt_file, "r", encoding="utf-8") as f:
                user_opt = json.load(f)
                options.update(user_opt)
        except Exception as e:
            print(f"[WARN] Errore lettura options.json: {e}")
    # Priorità a lan_port se impostata
    if "lan_port" not in options and "download_port" in options:
        options["lan_port"] = options["download_port"]
    return options


# ==============================================================================
# STREAMING CONDIVISO (RANGE REQUESTS PER DOWNLOAD VELOCE)
# ==============================================================================

def stream_active_file(handler: BaseHTTPRequestHandler, send_body: bool = True):
    """Invia il file attivo supportando Range Requests e streaming a blocchi."""
    file_path = STATE.active_file_path
    display_name = STATE.display_name
    client_ip = handler.client_address[0] if handler.client_address else "Client"

    if not file_path or not os.path.exists(file_path):
        handler.send_error(404, "Nessun file attivo o file rimosso dal server")
        return

    total_size = os.path.getsize(file_path)
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "application/octet-stream"

    range_header = handler.headers.get('Range')
    start = 0
    end = total_size - 1
    is_range = False

    if range_header and range_header.startswith('bytes='):
        try:
            parts = range_header[6:].split('-')
            if parts[0]:
                start = int(parts[0])
            if len(parts) > 1 and parts[1]:
                end = int(parts[1])
            if start <= end and start < total_size:
                is_range = True
                end = min(end, total_size - 1)
            else:
                handler.send_response(416, "Requested Range Not Satisfiable")
                handler.send_header("Content-Range", f"bytes */{total_size}")
                handler.end_headers()
                return
        except ValueError:
            is_range = False

    content_length = end - start + 1

    if is_range:
        handler.send_response(206, "Partial Content")
        handler.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
    else:
        handler.send_response(200, "OK")

    quoted_name = urllib.parse.quote(display_name)
    clean_ascii = display_name.encode('ascii', 'replace').decode('ascii').replace('"', '')

    handler.send_header("Content-Type", mime_type)
    handler.send_header("Content-Length", str(content_length))
    handler.send_header(
        "Content-Disposition",
        f'attachment; filename="{clean_ascii}"; filename*=UTF-8\'\'{quoted_name}'
    )
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()

    if not send_body:
        return

    STATE.add_log(f"Inizio download da {client_ip} ({STATE._format_size(content_length)})", "download")

    chunk_size = 64 * 1024
    bytes_sent = 0

    try:
        with open(file_path, 'rb') as f:
            f.seek(start)
            remaining = content_length
            while remaining > 0:
                read_len = min(chunk_size, remaining)
                chunk = f.read(read_len)
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)
                bytes_sent += len(chunk)

        STATE.add_log(f"Download completato verso {client_ip} ({STATE._format_size(bytes_sent)})", "download")
    except (BrokenPipeError, ConnectionResetError):
        STATE.add_log(f"Connessione chiusa anticipatamente da {client_ip}", "warn")
    except Exception as e:
        STATE.add_log(f"Errore download {client_ip}: {e}", "error")


# ==============================================================================
# INTERFACCIA WEB RESPONSIVE & MOBILE-FRIENDLY (HTML/CSS/JS)
# ==============================================================================

HTML_PORTAL = """<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Local Drop &amp; Share — Home Assistant</title>
    <style>
        :root {
            --bg: #0d1117;
            --card: #161b22;
            --border: #30363d;
            --accent: #00d2ff;
            --accent-glow: rgba(0, 210, 255, 0.25);
            --text: #f0f6fc;
            --muted: #8b949e;
            --success: #238636;
            --danger: #da3633;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background: var(--bg); color: var(--text); padding: 16px; max-width: 720px; margin: 0 auto; -webkit-tap-highlight-color: transparent; }
        header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
        .logo-title { font-size: 19px; font-weight: 800; color: #fff; letter-spacing: -0.3px; }
        .badge-status { padding: 5px 12px; border-radius: 20px; font-size: 11px; font-weight: 700; text-transform: uppercase; }
        .badge-online { background: rgba(35, 134, 54, 0.2); color: #3fb950; border: 1px solid #238636; }
        .badge-offline { background: #21262d; color: var(--muted); border: 1px solid #30363d; }

        .active-card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 20px; margin-bottom: 20px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
        .file-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
        .file-name { font-size: 16px; font-weight: 700; word-break: break-all; color: #fff; }
        .file-size { font-size: 13px; color: var(--muted); margin-top: 4px; }
        
        .btn-download-direct { display: block; width: 100%; background: linear-gradient(135deg, #00d2ff 0%, #0077ff 100%); color: #fff; text-decoration: none; text-align: center; font-size: 17px; font-weight: 700; padding: 15px; border-radius: 12px; margin-bottom: 16px; box-shadow: 0 4px 20px var(--accent-glow); }
        .btn-download-direct:active { transform: scale(0.98); }

        .qr-wrap { display: flex; flex-direction: column; align-items: center; margin: 16px 0; }
        .qr-card { background: #ffffff; padding: 14px; border-radius: 16px; display: inline-block; max-width: 220px; box-shadow: 0 8px 24px rgba(0,0,0,0.5); }
        .qr-card svg { width: 190px; height: 190px; display: block; }
        .qr-label { text-align: center; color: #475569; font-size: 11px; font-weight: 700; margin-top: 6px; }

        .url-bar { display: flex; gap: 8px; margin: 14px 0; }
        .url-input { flex: 1; background: #0d1117; border: 1px solid var(--border); color: #58a6ff; font-family: monospace; font-size: 12px; padding: 10px 12px; border-radius: 8px; }
        .btn-copy { background: #21262d; color: #c9d1d9; border: 1px solid var(--border); padding: 10px 16px; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 13px; }
        .btn-copy:active { background: #30363d; }

        .btn-stop { width: 100%; background: #21262d; color: #f85149; border: 1px solid #da3633; padding: 12px; border-radius: 10px; font-size: 14px; font-weight: 700; cursor: pointer; }
        .btn-stop:active { background: var(--danger); color: #fff; }

        .tabs { display: flex; gap: 8px; margin-bottom: 16px; }
        .tab-btn { flex: 1; background: #21262d; color: var(--muted); border: 1px solid var(--border); padding: 12px; border-radius: 10px; cursor: pointer; font-size: 13px; font-weight: 600; text-align: center; }
        .tab-btn.active { background: var(--accent); color: #0d1117; border-color: var(--accent); font-weight: 700; }

        .panel { display: none; }
        .panel.active { display: block; }

        .drop-box { border: 2px dashed #30363d; border-radius: 16px; padding: 36px 16px; text-align: center; background: #161b22; cursor: pointer; transition: 0.2s; }
        .drop-box.dragover { border-color: var(--accent); background: rgba(0,210,255,0.08); }
        .drop-icon { font-size: 44px; margin-bottom: 10px; }
        .drop-title { font-size: 16px; font-weight: 700; margin-bottom: 6px; color: #fff; }
        .drop-sub { font-size: 12px; color: var(--muted); margin-bottom: 18px; }
        
        .btn-pick-mobile { display: inline-block; background: #21262d; color: #58a6ff; border: 1px solid #30363d; padding: 12px 24px; border-radius: 10px; font-weight: 700; font-size: 14px; cursor: pointer; }
        .btn-pick-mobile:active { background: #30363d; color: #fff; }

        .browse-list { background: #161b22; border: 1px solid var(--border); border-radius: 12px; max-height: 280px; overflow-y: auto; padding: 6px; }
        .browse-item { display: flex; justify-content: space-between; align-items: center; padding: 10px 12px; border-bottom: 1px solid #21262d; }
        .browse-item:last-child { border-bottom: none; }
        .btn-share-item { background: #21262d; color: var(--accent); border: 1px solid rgba(0,210,255,0.3); padding: 6px 14px; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; }

        .progress-box { margin-top: 14px; display: none; }
        .progress-bar-wrap { width: 100%; height: 8px; background: #21262d; border-radius: 4px; overflow: hidden; margin-bottom: 6px; }
        .progress-bar { width: 0%; height: 100%; background: var(--accent); transition: width 0.1s; }
        .progress-text { font-size: 12px; color: var(--muted); text-align: center; }

        .log-box { background: #090d13; border: 1px solid #21262d; border-radius: 10px; padding: 10px; font-family: monospace; font-size: 11px; max-height: 120px; overflow-y: auto; margin-top: 20px; }
        .log-entry { margin-bottom: 4px; }
    </style>
</head>
<body>
    <header>
        <div>
            <div class="logo-title">Local Drop &amp; Share</div>
            <div style="font-size: 11px; color: var(--muted); margin-top: 2px;">Home Assistant &bull; Porta 5001 LAN</div>
        </div>
        <div id="statusBadge" class="badge-status badge-offline">● INATTIVO</div>
    </header>

    <!-- Card file condiviso attivo -->
    <div id="sharingCard" class="active-card" style="display: none;">
        <div class="file-row">
            <div>
                <div id="activeFileName" class="file-name">nome_file.ext</div>
                <div id="activeFileSize" class="file-size">Dimensione: 0 MB</div>
            </div>
            <span id="activeBadgeType" style="background: rgba(0,210,255,0.15); color: var(--accent); padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 700;">FILE</span>
        </div>

        <!-- Download Diretto con pulsantone comodo per Smartphone -->
        <a id="btnDirectDownload" href="#" class="btn-download-direct" download>📥 Scarica Subito</a>

        <!-- QR Code per condividere ad altri -->
        <div class="qr-wrap">
            <div class="qr-card">
                <div id="qrContainer"></div>
                <div class="qr-label">Inquadra con un altro smartphone</div>
            </div>
        </div>

        <!-- Barra URL con pulsante Copia -->
        <div class="url-bar">
            <input type="text" id="urlInput" class="url-input" readonly>
            <button id="btnCopy" class="btn-copy" onclick="copyUrl()">📋 Copia</button>
        </div>

        <button class="btn-stop" onclick="stopSharing()">⏹️ Ferma Condivisione</button>
    </div>

    <!-- Selettore modalità di caricamento -->
    <div class="tabs">
        <button class="tab-btn active" onclick="switchTab('upload')">📱 Carica dal Dispositivo</button>
        <button class="tab-btn" onclick="switchTab('ha')">📂 File Home Assistant</button>
    </div>

    <!-- Pannello Upload (supporta smartphone, tablet e PC drag & drop) -->
    <div id="panelUpload" class="panel active">
        <div id="dropBox" class="drop-box" onclick="document.getElementById('fileInput').click()">
            <div class="drop-icon">📤</div>
            <div class="drop-title">Tocca o Trascina un File da Condividere</div>
            <div class="drop-sub">Foto, Video o Documenti dal tuo cellulare/PC verranno salvati su Home Assistant e condivisi su LAN</div>
            <button type="button" class="btn-pick-mobile">📁 Sfoglia / Galleria / Fotocamera</button>
            <input type="file" id="fileInput" style="display: none;" onchange="handleFileSelect(this.files)">

            <div id="progressBox" class="progress-box">
                <div class="progress-bar-wrap">
                    <div id="progressBar" class="progress-bar"></div>
                </div>
                <div id="progressText" class="progress-text">Caricamento su Home Assistant... 0%</div>
            </div>
        </div>
    </div>

    <!-- Pannello File su Home Assistant (/share e /media) -->
    <div id="panelHA" class="panel">
        <div style="font-size: 12px; color: var(--muted); margin-bottom: 8px;">File trovati in /share e /media su Home Assistant:</div>
        <div id="browseList" class="browse-list">Caricamento in corso...</div>
    </div>

    <!-- Registro Attività -->
    <div style="margin-top: 20px; font-size: 11px; font-weight: 700; color: #8b949e; text-transform: uppercase;">
        Registro Attività &amp; Download LAN
    </div>
    <div id="logBox" class="log-box"></div>

    <script>
        let currentTab = 'upload';

        function switchTab(tab) {
            currentTab = tab;
            document.querySelectorAll('.tab-btn').forEach((b, idx) => {
                b.classList.toggle('active', (tab === 'upload' && idx === 0) || (tab === 'ha' && idx === 1));
            });
            document.getElementById('panelUpload').classList.toggle('active', tab === 'upload');
            document.getElementById('panelHA').classList.toggle('active', tab === 'ha');
            if (tab === 'ha') loadHAFiles();
        }

        // Drag and Drop (per browser desktop)
        const dropBox = document.getElementById('dropBox');
        ['dragenter', 'dragover'].forEach(e => {
            dropBox.addEventListener(e, ev => { ev.preventDefault(); dropBox.classList.add('dragover'); });
        });
        ['dragleave', 'drop'].forEach(e => {
            dropBox.addEventListener(e, ev => { ev.preventDefault(); dropBox.classList.remove('dragover'); });
        });
        dropBox.addEventListener('drop', ev => {
            ev.preventDefault();
            dropBox.classList.remove('dragover');
            if (ev.dataTransfer.files.length > 0) handleFileSelect(ev.dataTransfer.files);
        });

        function handleFileSelect(files) {
            if (!files || files.length === 0) return;
            const file = files[0];
            uploadStream(file);
        }

        function uploadStream(file) {
            const pBox = document.getElementById('progressBox');
            const pBar = document.getElementById('progressBar');
            const pText = document.getElementById('progressText');
            pBox.style.display = 'block';
            pBar.style.width = '0%';
            pText.innerText = `Caricamento di ${file.name}... (0%)`;

            const xhr = new XMLHttpRequest();
            xhr.open('POST', './api/upload?filename=' + encodeURIComponent(file.name), true);

            xhr.upload.onprogress = ev => {
                if (ev.lengthComputable) {
                    const pct = Math.round((ev.loaded / ev.total) * 100);
                    const mbSent = (ev.loaded / (1024*1024)).toFixed(1);
                    const mbTotal = (ev.total / (1024*1024)).toFixed(1);
                    pBar.style.width = pct + '%';
                    pText.innerText = `Caricamento: ${pct}% (${mbSent} / ${mbTotal} MB)`;
                }
            };

            xhr.onload = () => {
                pBox.style.display = 'none';
                if (xhr.status === 200) {
                    updateStatus();
                } else {
                    alert('Errore durante il caricamento: ' + xhr.responseText);
                }
            };

            xhr.onerror = () => {
                pBox.style.display = 'none';
                alert('Errore di connessione con Home Assistant.');
            };

            xhr.send(file);
        }

        function stopSharing() {
            fetch('./api/stop', { method: 'POST' }).then(() => updateStatus());
        }

        function copyUrl() {
            const inp = document.getElementById('urlInput');
            inp.select();
            inp.setSelectionRange(0, 99999);
            navigator.clipboard ? navigator.clipboard.writeText(inp.value) : document.execCommand('copy');
            const btn = document.getElementById('btnCopy');
            btn.innerText = '✓ Copiato!';
            setTimeout(() => { btn.innerText = '📋 Copia'; }, 1800);
        }

        function loadHAFiles() {
            const listEl = document.getElementById('browseList');
            listEl.innerHTML = '<div style="padding:10px;color:#8b949e;">Scansione /share e /media su Home Assistant...</div>';
            fetch('./api/browse')
                .then(r => r.json())
                .then(data => {
                    if (!data.files || data.files.length === 0) {
                        listEl.innerHTML = '<div style="padding:10px;color:#8b949e;">Nessun file trovato in /share o /media.</div>';
                        return;
                    }
                    let html = '';
                    data.files.forEach(f => {
                        html += `
                        <div class="browse-item">
                            <div style="max-width: 75%;">
                                <div style="font-weight:600; font-size:13px; color:#fff; word-break:break-all;">${f.name}</div>
                                <div style="font-size:11px; color:#8b949e;">${f.path} &bull; ${f.size}</div>
                            </div>
                            <button class="btn-share-item" onclick="shareExistingFile('${encodeURIComponent(f.path)}')">Condividi</button>
                        </div>`;
                    });
                    listEl.innerHTML = html;
                })
                .catch(err => {
                    listEl.innerHTML = '<div style="padding:10px;color:#f85149;">Errore lettura file: ' + err + '</div>';
                });
        }

        function shareExistingFile(encodedPath) {
            fetch('./api/share_existing?path=' + encodedPath, { method: 'POST' })
                .then(() => {
                    switchTab('upload');
                    updateStatus();
                });
        }

        function updateStatus() {
            fetch('./api/status')
                .then(r => r.json())
                .then(data => {
                    const badge = document.getElementById('statusBadge');
                    const card = document.getElementById('sharingCard');
                    const dlBtn = document.getElementById('btnDirectDownload');

                    if (data.active) {
                        badge.className = 'badge-status badge-online';
                        badge.innerText = `● ATTIVO :${data.lan_port}`;
                        card.style.display = 'block';

                        document.getElementById('activeFileName').innerText = data.file_name;
                        document.getElementById('activeFileSize').innerText = `Dimensione: ${data.file_size_formatted}`;
                        document.getElementById('qrContainer').innerHTML = data.qr_svg;
                        document.getElementById('urlInput').value = data.download_url;

                        dlBtn.href = data.download_url;
                        dlBtn.setAttribute('download', data.file_name);
                        dlBtn.innerText = `📥 Scarica Subito (${data.file_size_formatted})`;
                    } else {
                        badge.className = 'badge-status badge-offline';
                        badge.innerText = '● INATTIVO';
                        card.style.display = 'none';
                    }

                    // Render Logs
                    const logBox = document.getElementById('logBox');
                    let logHtml = '';
                    (data.logs || []).forEach(l => {
                        let col = '#58a6ff';
                        if (l.level === 'download') col = '#3fb950';
                        else if (l.level === 'warn') col = '#d29922';
                        else if (l.level === 'error') col = '#f85149';
                        logHtml += `<div class="log-entry"><span style="color:#6e7681;">[${l.time}]</span> <span style="color:${col};">${l.msg}</span></div>`;
                    });
                    logBox.innerHTML = logHtml;
                    logBox.scrollTop = logBox.scrollHeight;
                })
                .catch(() => {});
        }

        setInterval(updateStatus, 3000);
        updateStatus();
    </script>
</body>
</html>"""


# ==============================================================================
# GESTORE RICHIESTE UNIFICATO (USATO SIA SU PORTA 5001 CHE SU 8099)
# ==============================================================================

class UnifiedShareHandler(BaseHTTPRequestHandler):
    """
    Gestore HTTP unificato per Ingress (8099) e LAN (5001):
    - GET / -> Portale Web Responsive (Upload da smartphone/PC, QR code, pulsante download)
    - GET /api/status -> Stato JSON
    - GET /api/browse -> Esplorazione cartelle /share e /media
    - POST /api/upload -> Ricezione streaming di file caricati via browser/smartphone
    - POST /api/share_existing -> Selezione file da Home Assistant
    - POST /api/stop -> Interruzione condivisione
    - GET /<nome_file> -> Download diretto del file con Range Requests (HTTP 206)
    """

    def log_message(self, format, *args):
        pass  # Evita log rumorosi su console standard

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path).path.strip('/')
        if parsed and parsed not in ['', 'index.html']:
            stream_active_file(self, send_body=False)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path.rstrip('/')

        # 1. Dashboard Principale
        if path == '' or path.endswith('/'):
            self._send_html(HTML_PORTAL)
            return

        # 2. API di Stato
        if path.endswith('/api/status'):
            self._send_json(STATE.get_status())
            return

        # 3. API Esplora File Home Assistant
        if path.endswith('/api/browse'):
            files = self._list_ha_storage()
            self._send_json({"files": files})
            return

        # 4. Download Diretto del file attivo
        # Se viene richiesto un percorso che corrisponde al nome del file attivo o /download
        decoded_name = urllib.parse.unquote(path.lstrip('/'))
        if STATE.active_file_path:
            active_name = STATE.display_name
            if decoded_name == active_name or decoded_name == 'download' or path.endswith(f"/{urllib.parse.quote(active_name)}"):
                stream_active_file(self, send_body=True)
                return

        # Se non corrisponde, rimanda alla homepage
        self._send_html(HTML_PORTAL)

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path.rstrip('/')

        # Upload in streaming (da smartphone o PC)
        if path.endswith('/api/upload'):
            query = urllib.parse.parse_qs(parsed_url.query)
            filename = query.get('filename', ['upload.bin'])[0]
            clean_name = os.path.basename(filename)

            temp_dir = "/share/drop_share_temp" if os.path.exists("/share") else "/tmp/drop_share_temp"
            os.makedirs(temp_dir, exist_ok=True)
            dest_path = os.path.join(temp_dir, clean_name)

            content_length = int(self.headers.get('Content-Length', 0))
            chunk_size = 64 * 1024
            remaining = content_length

            # Scrittura a blocchi diretta da socket a disco (RAM minima invariata)
            with open(dest_path, 'wb') as f:
                while remaining > 0:
                    read_len = min(chunk_size, remaining)
                    chunk = self.rfile.read(read_len)
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)

            client_ip = self.client_address[0] if self.client_address else "Dispositivo"
            STATE.add_log(f"Nuovo file caricato da {client_ip}: {clean_name}", "info")
            STATE.set_file(dest_path, clean_name, is_temp=True, is_zip=clean_name.endswith('.zip'))
            self._send_json({"status": "ok", "file": clean_name})
            return

        # Condividi file già presente su HA
        if path.endswith('/api/share_existing'):
            query = urllib.parse.parse_qs(parsed_url.query)
            file_path = query.get('path', [''])[0]
            if file_path and os.path.exists(file_path):
                display_name = os.path.basename(file_path)
                STATE.set_file(file_path, display_name, is_temp=False, is_zip=file_path.endswith('.zip'))
                self._send_json({"status": "ok"})
            else:
                self.send_error(404, "File non trovato sul filesystem di Home Assistant")
            return

        # Arresta condivisione
        if path.endswith('/api/stop'):
            STATE.stop_sharing()
            self._send_json({"status": "stopped"})
            return

        self.send_error(404, "Endpoint non trovato")

    def _send_html(self, html: str):
        encoded = html.encode('utf-8')
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, data: dict):
        encoded = json.dumps(data).encode('utf-8')
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(encoded)

    def _list_ha_storage(self) -> list[dict]:
        """Esplora le cartelle /share e /media di Home Assistant."""
        found = []
        for base in ["/share", "/media"]:
            if os.path.exists(base):
                for root, _, files in os.walk(base):
                    for file in files:
                        full_p = os.path.join(root, file)
                        try:
                            sz = os.path.getsize(full_p)
                            found.append({
                                "name": file,
                                "path": full_p,
                                "size": STATE._format_size(sz)
                            })
                        except Exception:
                            pass
                        if len(found) >= 50:
                            break
                    if len(found) >= 50:
                        break
        return found


# ==============================================================================
# PULIZIA PERIODICA FILE TEMPORANEI (BACKGROUND WORKER)
# ==============================================================================

def cleanup_worker(clean_hours: int):
    """Pulisce i file temporanei caricati dopo clean_hours ore."""
    while True:
        time.sleep(3600)
        temp_dir = "/share/drop_share_temp" if os.path.exists("/share") else "/tmp/drop_share_temp"
        if os.path.exists(temp_dir):
            now = time.time()
            max_age = clean_hours * 3600
            for f in os.listdir(temp_dir):
                fp = os.path.join(temp_dir, f)
                try:
                    if os.path.isfile(fp) and (now - os.path.getmtime(fp)) > max_age:
                        if fp != STATE.active_file_path:
                            os.remove(fp)
                except Exception:
                    pass


# ==============================================================================
# AVVIO SERVIZI SULLE PORTE 5001 E 8099
# ==============================================================================

def main():
    options = load_options()
    lan_port = int(options.get("lan_port", 5001))
    lan_ip_override = options.get("lan_ip_override", "")
    auto_clean_hours = int(options.get("auto_clean_hours", 24))

    host_ip = detect_host_ip(lan_ip_override)

    STATE.lan_port = lan_port
    STATE.lan_ip = host_ip
    STATE.ingress_port = 8099

    print("==========================================================")
    print(" Local Drop & Share per Home Assistant")
    print(f" -> IP Host LAN rilevato: {host_ip}")
    print(f" -> Porta Web & Download LAN: {lan_port}")
    print(f" -> Porta Ingress Sidebar HA: 8099")
    print(f" -> URL Web LAN:          http://{host_ip}:{lan_port}/")
    print("==========================================================")

    STATE.add_log(f"Server avviato su http://{host_ip}:{lan_port}/", "info")

    # Server 1: Porta LAN 5001 (accessibile da smartphone, tablet e PC sulla rete locale)
    lan_server = ThreadingHTTPServer(('0.0.0.0', lan_port), UnifiedShareHandler)
    t_lan = threading.Thread(target=lan_server.serve_forever, daemon=True)
    t_lan.start()

    # Worker per pulizia automatica
    t_clean = threading.Thread(target=cleanup_worker, args=(auto_clean_hours,), daemon=True)
    t_clean.start()

    # Server 2: Porta Ingress 8099 (Home Assistant sidebar)
    ingress_server = ThreadingHTTPServer(('0.0.0.0', 8099), UnifiedShareHandler)
    try:
        ingress_server.serve_forever()
    except KeyboardInterrupt:
        print("\nArresto server in corso...")
    finally:
        lan_server.shutdown()
        ingress_server.shutdown()


if __name__ == "__main__":
    main()
