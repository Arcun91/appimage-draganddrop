#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local Drop & Share
==================
Un'applicazione desktop nativa per Linux progettata per condividere file o intere
cartelle istantaneamente sulla propria rete LAN/Wi-Fi locale tramite un server web
temporaneo multi-thread e un codice QR visualizzato a schermo.

Caratteristiche:
- 100% Locale / Zero Cloud: il traffico resta confinato alla rete locale.
- Server HTTP multi-thread con supporto per Range Requests (resume download su smartphone).
- Rilevamento automatico dell'IP LAN primario e selezione interfaccia di rete.
- Generazione immediata di QR Code scansionabile da qualsiasi smartphone.
- Drag & Drop nativo per file o intere cartelle (con compressione ZIP automatica).
- Interfaccia grafica dark moderna (PyQt6), adatta ad ambienti desktop Linux e packaging AppImage.
"""

import os
import sys
import io
import time
import socket
import tempfile
import zipfile
import mimetypes
import threading
import urllib.parse
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# Import GUI & QR Code
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtCore import Qt, pyqtSignal, QObject
import qrcode

# Opzionale: psutil per un'enumerazione avanzata delle interfacce di rete
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


# ==============================================================================
# 1. UTILITÀ DI RETE (NETWORK UTILITIES)
# ==============================================================================

class NetworkUtils:
    """Funzioni helper per l'individuazione degli indirizzi IP e delle porte libere."""

    @staticmethod
    def get_primary_lan_ip() -> str:
        """
        Individua l'IP LAN primario usato per il routing verso l'esterno
        tramite un socket UDP verso un indirizzo fittizio (non invia pacchetti reali).
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # 10.255.255.255 è un IP non instradabile che permette al kernel di
            # determinare l'interfaccia usata dalla route di default.
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

    @staticmethod
    def get_all_interfaces() -> list[tuple[str, str]]:
        """
        Ritorna una lista di tuple (nome_interfaccia, indirizzo_ip) per tutte
        le interfacce di rete attive. L'IP primario viene posizionato per primo.
        """
        interfaces: list[tuple[str, str]] = []
        primary_ip = NetworkUtils.get_primary_lan_ip()

        if HAS_PSUTIL:
            try:
                for iface_name, addrs in psutil.net_if_addrs().items():
                    for addr in addrs:
                        if addr.family == socket.AF_INET:
                            ip = addr.address
                            if not ip.startswith('127.'):
                                interfaces.append((iface_name, ip))
            except Exception:
                pass

        # Fallback se psutil non è disponibile o non trova nulla
        if not interfaces:
            interfaces.append(('LAN / Wi-Fi', primary_ip))

        # Assicura che l'IP primario sia in testa alla lista
        interfaces.sort(key=lambda x: (0 if x[1] == primary_ip else 1, x[0]))

        # Aggiungi sempre localhost alla fine come opzione di test
        if not any(ip == '127.0.0.1' for _, ip in interfaces):
            interfaces.append(('Localhost', '127.0.0.1'))

        return interfaces

    @staticmethod
    def find_free_port(start_port: int = 8000, max_port: int = 8099) -> int:
        """Cerca una porta TCP libera a partire da start_port."""
        for port in range(start_port, max_port + 1):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(('0.0.0.0', port))
                    return port
                except OSError:
                    continue
        # Se tutte le porte nel range sono occupate, lascia che l'OS ne assegni una libera
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('0.0.0.0', 0))
            return s.getsockname()[1]


# ==============================================================================
# 2. SERVER HTTP CON RANGE REQUEST E STREAMING IN THREAD SEPARATO
# ==============================================================================

class ShareServerSignals(QObject):
    """Segnali Qt per notificare la GUI degli eventi del server in modo thread-safe."""
    server_started = pyqtSignal(str, int)            # ip, port
    server_stopped = pyqtSignal()
    server_error = pyqtSignal(str)
    log_message = pyqtSignal(str, str)               # text, level ('info', 'download', 'warn', 'error')
    transfer_progress = pyqtSignal(str, int, int)    # client_ip, bytes_sent, total_bytes
    transfer_completed = pyqtSignal(str, str, int)   # client_ip, filename, total_bytes


class ShareHTTPRequestHandler(BaseHTTPRequestHandler):
    """
    Gestore delle richieste HTTP per lo sharing.
    Supporta:
    - Pagina di download mobile-friendly responsive per chi visita la root (/)
    - Download diretto del file con Content-Disposition: attachment
    - Supporto per Range Requests (HTTP 206 Partial Content) essenziale per iOS/Android
    - Streaming a blocchi (64 KB) per evitare consumo eccessivo di RAM
    """

    server: 'CustomThreadingServer'

    def log_message(self, format, *args):
        """Reindirizza i log interni verso il segnale Qt invece che su stderr."""
        msg = format % args
        if self.server and hasattr(self.server, 'signals'):
            self.server.signals.log_message.emit(f"HTTP: {msg}", "info")

    def _get_client_ip(self) -> str:
        return self.client_address[0] if self.client_address else "Client sconosciuto"

    def _human_size(self, size_bytes: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"

    def do_HEAD(self):
        """Gestisce le richieste HEAD restituendo gli header corretti."""
        self._serve_file(send_body=False)

    def do_GET(self):
        """Gestisce le richieste GET."""
        parsed_path = urllib.parse.urlparse(self.path).path
        decoded_path = urllib.parse.unquote(parsed_path).strip('/')

        # Se la richiesta è per la root o per /download-page, mostra la pagina HTML
        if decoded_path == '' or decoded_path == 'index.html':
            self._serve_landing_page()
        else:
            # Qualsiasi altro path scarica il file condiviso
            self._serve_file(send_body=True)

    def _serve_landing_page(self):
        """Restituisce una pagina HTML dark, moderna e responsive per browser mobile."""
        filename = self.server.display_name
        file_size = self.server.file_size
        size_str = self._human_size(file_size)
        is_zip = self.server.is_folder
        type_str = "Archivio Cartella (ZIP)" if is_zip else "File"
        encoded_filename = urllib.parse.quote(filename)
        download_url = f"/{encoded_filename}"

        client_ip = self._get_client_ip()
        self.server.signals.log_message.emit(
            f"Connessione da {client_ip} → Visualizzazione pagina download", "info"
        )

        html = f"""<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Local Drop &amp; Share - {filename}</title>
    <style>
        :root {{
            --bg-color: #0f1117;
            --card-bg: #1a1d27;
            --border-color: #2b3042;
            --accent-color: #00d2ff;
            --accent-glow: rgba(0, 210, 255, 0.35);
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }}
        body {{
            background-color: var(--bg-color);
            color: var(--text-primary);
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            padding: 20px;
        }}
        .card {{
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 32px 24px;
            max-width: 480px;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 40px rgba(0,0,0,0.6);
        }}
        .badge {{
            display: inline-block;
            background: rgba(0, 210, 255, 0.15);
            color: var(--accent-color);
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 20px;
            border: 1px solid rgba(0, 210, 255, 0.3);
        }}
        .icon {{
            font-size: 64px;
            margin-bottom: 16px;
            display: inline-block;
        }}
        h1 {{
            font-size: 20px;
            font-weight: 700;
            word-break: break-all;
            margin-bottom: 8px;
            color: #ffffff;
        }}
        .file-info {{
            font-size: 15px;
            color: var(--text-secondary);
            margin-bottom: 28px;
        }}
        .btn-download {{
            display: block;
            background: linear-gradient(135deg, #00d2ff 0%, #0077ff 100%);
            color: #ffffff;
            text-decoration: none;
            font-size: 18px;
            font-weight: 700;
            padding: 16px 24px;
            border-radius: 14px;
            box-shadow: 0 10px 25px var(--accent-glow);
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }}
        .btn-download:active {{
            transform: scale(0.98);
        }}
        .footer {{
            margin-top: 24px;
            font-size: 12px;
            color: #6b7280;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="badge">📡 Condivisione LAN Locale</div>
        <div class="icon">{'📦' if is_zip else '📄'}</div>
        <h1>{filename}</h1>
        <div class="file-info">{type_str} &bull; <strong>{size_str}</strong></div>
        <a href="{download_url}" class="btn-download" download="{filename}">
            📥 Scarica Subito ({size_str})
        </a>
        <div class="footer">Trasferimento diretto P2P nella rete Wi-Fi locale &bull; Nessun cloud</div>
    </div>
</body>
</html>"""
        encoded_html = html.encode('utf-8')
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded_html)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded_html)

    def _serve_file(self, send_body: bool = True):
        """Invia il file con supporto allo streaming e a HTTP Range Requests."""
        file_path = self.server.file_path
        filename = self.server.display_name
        total_size = self.server.file_size
        client_ip = self._get_client_ip()

        if not os.path.exists(file_path):
            self.send_error(404, "File non trovato sul server locale")
            return

        # Rilevamento MIME type
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "application/octet-stream"

        # Gestione header Range (se presente)
        range_header = self.headers.get('Range')
        start = 0
        end = total_size - 1
        is_range = False

        if range_header and range_header.startswith('bytes='):
            try:
                ranges = range_header[6:].split('-')
                if ranges[0]:
                    start = int(ranges[0])
                if len(ranges) > 1 and ranges[1]:
                    end = int(ranges[1])
                if start <= end and start < total_size:
                    is_range = True
                    end = min(end, total_size - 1)
                else:
                    self.send_response(416, "Requested Range Not Satisfiable")
                    self.send_header("Content-Range", f"bytes */{total_size}")
                    self.end_headers()
                    return
            except ValueError:
                is_range = False

        content_length = end - start + 1

        if is_range:
            self.send_response(206, "Partial Content")
            self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
        else:
            self.send_response(200, "OK")

        # Header di download
        quoted_filename = urllib.parse.quote(filename)
        clean_ascii_filename = filename.encode('ascii', 'replace').decode('ascii').replace('"', '')
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{clean_ascii_filename}"; filename*=UTF-8\'\'{quoted_filename}'
        )
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        if not send_body:
            return

        # Notifica inizio trasferimento alla GUI
        self.server.signals.log_message.emit(
            f"Inizio download da {client_ip} ({self._human_size(content_length)})", "download"
        )

        chunk_size = 64 * 1024  # 64 KB per chunk
        bytes_sent = 0

        try:
            with open(file_path, 'rb') as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    if getattr(self.server, 'stop_requested', False):
                        break
                    read_bytes = min(chunk_size, remaining)
                    chunk = f.read(read_bytes)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
                    bytes_sent += len(chunk)

            # Notifica successo
            self.server.signals.log_message.emit(
                f"Download completato con successo verso {client_ip} ({self._human_size(bytes_sent)})", "download"
            )
            self.server.signals.transfer_completed.emit(client_ip, filename, bytes_sent)

        except (BrokenPipeError, ConnectionResetError):
            self.server.signals.log_message.emit(
                f"Client {client_ip} ha interrotto la connessione (download parziale)", "warn"
            )
        except Exception as e:
            self.server.signals.log_message.emit(
                f"Errore durante lo streaming verso {client_ip}: {e}", "error"
            )


class CustomThreadingServer(ThreadingHTTPServer):
    """Server HTTP multi-thread che memorizza i dati del file condiviso e i segnali Qt."""
    def __init__(self, server_address, RequestHandlerClass, file_path: str, display_name: str,
                 is_folder: bool, file_size: int, signals: ShareServerSignals):
        super().__init__(server_address, RequestHandlerClass)
        self.file_path = file_path
        self.display_name = display_name
        self.is_folder = is_folder
        self.file_size = file_size
        self.signals = signals
        self.stop_requested = False


class ShareServerThread(QtCore.QThread):
    """
    QThread per eseguire il server HTTP in background senza bloccare la GUI.
    Gestisce l'avvio, il ciclo di vita e l'arresto pulito delle connessioni.
    """
    def __init__(self, file_path: str, display_name: str, is_folder: bool, file_size: int,
                 port: int, signals: ShareServerSignals, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.display_name = display_name
        self.is_folder = is_folder
        self.file_size = file_size
        self.port = port
        self.signals = signals
        self.server: CustomThreadingServer | None = None

    def run(self):
        try:
            # Ascolta su tutte le interfacce (0.0.0.0) per permettere l'accesso da tutta la LAN
            self.server = CustomThreadingServer(
                ('0.0.0.0', self.port),
                ShareHTTPRequestHandler,
                self.file_path,
                self.display_name,
                self.is_folder,
                self.file_size,
                self.signals
            )
            self.signals.server_started.emit('0.0.0.0', self.port)
            self.server.serve_forever(poll_interval=0.5)
        except Exception as e:
            self.signals.server_error.emit(str(e))
        finally:
            if self.server:
                try:
                    self.server.server_close()
                except Exception:
                    pass
            self.signals.server_stopped.emit()

    def stop(self):
        """Arresta in modo pulito il server HTTP."""
        if self.server:
            self.server.stop_requested = True
            # shutdown() sveglia il ciclo serve_forever()
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        self.quit()
        self.wait(1500)


# ==============================================================================
# 3. GENERAZIONE DEL QR CODE
# ==============================================================================

class QRCodeGenerator:
    """Genera immagini QR Code nitide e ad alto contrasto per la scansione da smartphone."""

    @staticmethod
    def generate_pixmap(url: str, size: int = 240) -> QtGui.QPixmap:
        """
        Crea un QPixmap contenente il QR Code per l'URL specificato.
        Utilizza un buffer in memoria (BytesIO) per evitare scritture su disco.
        """
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2
        )
        qr.add_data(url)
        qr.make(fit=True)

        # Usiamo moduli neri su sfondo bianco: la fotocamera dello smartphone
        # riconosce all'istante i QR code standard ad alto contrasto.
        pil_img = qr.make_image(fill_color="#111827", back_color="#ffffff")

        buf = io.BytesIO()
        pil_img.save(buf, format='PNG')
        buf.seek(0)

        pixmap = QtGui.QPixmap()
        pixmap.loadFromData(buf.getvalue(), 'PNG')

        # Scala dolcemente alla dimensione desiderata
        return pixmap.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )


# ==============================================================================
# 4. COMPONENTI GRAFICI DELL'INTERFACCIA (GUI)
# ==============================================================================

class DropZoneWidget(QtWidgets.QFrame):
    """
    Area di Drag-and-Drop moderna con bordo tratteggiato ed effetto hover luminoso.
    Accetta il rilascio di file singoli o intere cartelle dal file manager Linux.
    """
    path_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.is_drag_active = False
        self._init_ui()

    def _init_ui(self):
        self.setObjectName("dropZone")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(30, 40, 30, 40)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Icona SVG / Carattere grafico
        self.icon_label = QtWidgets.QLabel("📥")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("font-size: 58px; background: transparent;")
        layout.addWidget(self.icon_label)

        # Titolo principale
        self.title_label = QtWidgets.QLabel("Trascina qui il file o la cartella")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 700; color: #f3f4f6; background: transparent;")
        layout.addWidget(self.title_label)

        # Sottotitolo
        self.subtitle_label = QtWidgets.QLabel("Verrà generato un QR Code per scaricarlo subito dallo smartphone")
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label.setStyleSheet("font-size: 13px; color: #9ca3af; background: transparent;")
        layout.addWidget(self.subtitle_label)

        # Separatore visivo "oppure"
        or_label = QtWidgets.QLabel("— OPPURE SFOGLIA —")
        or_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        or_label.setStyleSheet("font-size: 11px; font-weight: 600; color: #6b7280; margin-top: 10px; background: transparent;")
        layout.addWidget(or_label)

        # Pulsanti di selezione manuale
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_browse_file = QtWidgets.QPushButton("📄 Sfoglia File...")
        self.btn_browse_file.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_browse_file.setObjectName("btnBrowse")
        self.btn_browse_file.clicked.connect(self._browse_file)
        btn_layout.addWidget(self.btn_browse_file)

        self.btn_browse_folder = QtWidgets.QPushButton("📁 Sfoglia Cartella...")
        self.btn_browse_folder.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_browse_folder.setObjectName("btnBrowse")
        self.btn_browse_folder.clicked.connect(self._browse_folder)
        btn_layout.addWidget(self.btn_browse_folder)

        layout.addLayout(btn_layout)
        self._update_style()

    def _update_style(self):
        if self.is_drag_active:
            self.setStyleSheet("""
                #dropZone {
                    background-color: rgba(0, 210, 255, 0.08);
                    border: 2px dashed #00d2ff;
                    border-radius: 16px;
                }
            """)
        else:
            self.setStyleSheet("""
                #dropZone {
                    background-color: #171923;
                    border: 2px dashed #2d3345;
                    border-radius: 16px;
                }
                #dropZone:hover {
                    border-color: #3b82f6;
                    background-color: #1b1e2b;
                }
                #btnBrowse {
                    background-color: #232838;
                    color: #e5e7eb;
                    border: 1px solid #374151;
                    padding: 9px 18px;
                    border-radius: 8px;
                    font-size: 13px;
                    font-weight: 600;
                }
                #btnBrowse:hover {
                    background-color: #2d3449;
                    border-color: #00d2ff;
                    color: #ffffff;
                }
            """)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.is_drag_active = True
            self._update_style()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent):
        self.is_drag_active = False
        self._update_style()

    def dropEvent(self, event: QtGui.QDropEvent):
        self.is_drag_active = False
        self._update_style()
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            if file_path and os.path.exists(file_path):
                self.path_selected.emit(file_path)

    def _browse_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Seleziona il file da condividere in rete LAN"
        )
        if path:
            self.path_selected.emit(path)

    def _browse_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Seleziona la cartella da condividere in rete LAN"
        )
        if folder:
            self.path_selected.emit(folder)


# ==============================================================================
# 5. FINESTRA PRINCIPALE (MAIN APPLICATION WINDOW)
# ==============================================================================

class MainWindow(QtWidgets.QMainWindow):
    """Finestra principale dell'applicazione Local Drop & Share."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Local Drop & Share — Condivisione File su LAN")
        self.setMinimumSize(720, 760)
        self.resize(740, 780)

        # Variabili di stato
        self.server_thread: ShareServerThread | None = None
        self.server_signals = ShareServerSignals()
        self.active_file_path: str | None = None
        self.active_temp_zip: str | None = None
        self.current_port: int = 8000
        self.current_ip: str = NetworkUtils.get_primary_lan_ip()
        self.interfaces: list[tuple[str, str]] = NetworkUtils.get_all_interfaces()

        # Connessione segnali server
        self.server_signals.server_started.connect(self._on_server_started)
        self.server_signals.server_stopped.connect(self._on_server_stopped)
        self.server_signals.server_error.connect(self._on_server_error)
        self.server_signals.log_message.connect(self._append_log)
        self.server_signals.transfer_completed.connect(self._on_transfer_completed)

        self._setup_ui()
        self._apply_global_theme()

        # Log iniziale
        self._append_log(f"Applicazione pronta. IP LAN rilevato: {self.current_ip}", "info")

    def _setup_ui(self):
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        # -------------------------------------------------------------
        # 1. Header con Nome App, Versione e Badge di Stato
        # -------------------------------------------------------------
        header_layout = QtWidgets.QHBoxLayout()

        title_container = QtWidgets.QVBoxLayout()
        title_container.setSpacing(2)

        app_title = QtWidgets.QLabel("Local Drop & Share")
        app_title.setStyleSheet("font-size: 20px; font-weight: 800; color: #ffffff;")

        app_subtitle = QtWidgets.QLabel("Condividi file e cartelle via Wi-Fi istantaneamente con un QR Code")
        app_subtitle.setStyleSheet("font-size: 12px; color: #9ca3af;")

        title_container.addWidget(app_title)
        title_container.addWidget(app_subtitle)
        header_layout.addLayout(title_container)
        header_layout.addStretch()

        # Badge di stato
        self.status_badge = QtWidgets.QLabel("● INATTIVO")
        self.status_badge.setObjectName("statusBadgeInactive")
        header_layout.addWidget(self.status_badge)

        main_layout.addLayout(header_layout)

        # -------------------------------------------------------------
        # 2. QStackedWidget: Passaggio dinamico tra DropZone e Condivisione
        # -------------------------------------------------------------
        self.stack = QtWidgets.QStackedWidget()

        # Pagina 0: Area di rilascio
        self.drop_zone = DropZoneWidget()
        self.drop_zone.path_selected.connect(self.start_sharing)
        self.stack.addWidget(self.drop_zone)

        # Pagina 1: Dashboard di condivisione attiva
        self.sharing_view = QtWidgets.QWidget()
        self._build_sharing_view()
        self.stack.addWidget(self.sharing_view)

        main_layout.addWidget(self.stack, stretch=1)

        # -------------------------------------------------------------
        # 3. Console di Log in Stile Terminale
        # -------------------------------------------------------------
        log_header_layout = QtWidgets.QHBoxLayout()
        log_title = QtWidgets.QLabel("Registro Attività & Connessioni LAN")
        log_title.setStyleSheet("font-size: 12px; font-weight: 700; color: #cbd5e1; text-transform: uppercase;")
        log_header_layout.addWidget(log_title)
        log_header_layout.addStretch()

        btn_clear_log = QtWidgets.QPushButton("Pulisci")
        btn_clear_log.setObjectName("btnClearLog")
        btn_clear_log.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))
        btn_clear_log.clicked.connect(self._clear_logs)
        log_header_layout.addWidget(btn_clear_log)

        main_layout.addLayout(log_header_layout)

        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("terminalLog")
        self.log_text.setMaximumHeight(140)
        main_layout.addWidget(self.log_text)

    def _build_sharing_view(self):
        """Costruisce il pannello mostrato quando una condivisione è attiva."""
        layout = QtWidgets.QVBoxLayout(self.sharing_view)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(14)

        # Card con i dettagli del file
        self.file_card = QtWidgets.QFrame()
        self.file_card.setObjectName("infoCard")
        file_card_layout = QtWidgets.QHBoxLayout(self.file_card)
        file_card_layout.setContentsMargins(16, 12, 16, 12)

        self.file_icon_label = QtWidgets.QLabel("📄")
        self.file_icon_label.setStyleSheet("font-size: 28px;")
        file_card_layout.addWidget(self.file_icon_label)

        file_text_layout = QtWidgets.QVBoxLayout()
        file_text_layout.setSpacing(2)

        self.lbl_file_name = QtWidgets.QLabel("nome_del_file.zip")
        self.lbl_file_name.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffffff;")
        self.lbl_file_name.setWordWrap(True)

        self.lbl_file_details = QtWidgets.QLabel("Dimensione: -- MB • Percorso locale")
        self.lbl_file_details.setStyleSheet("font-size: 12px; color: #9ca3af;")

        file_text_layout.addWidget(self.lbl_file_name)
        file_text_layout.addWidget(self.lbl_file_details)
        file_card_layout.addLayout(file_text_layout, stretch=1)

        # Badge tipo (FILE o ZIP)
        self.badge_type = QtWidgets.QLabel("FILE")
        self.badge_type.setObjectName("typeBadge")
        file_card_layout.addWidget(self.badge_type)

        layout.addWidget(self.file_card)

        # Area Centrale: QR Code ad alto contrasto
        qr_container = QtWidgets.QHBoxLayout()
        qr_container.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Card bianca per il QR code (massima leggibilità fotocamera)
        self.qr_card = QtWidgets.QFrame()
        self.qr_card.setObjectName("qrCard")
        qr_card_layout = QtWidgets.QVBoxLayout(self.qr_card)
        qr_card_layout.setContentsMargins(14, 14, 14, 14)
        qr_card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lbl_qrcode = QtWidgets.QLabel()
        self.lbl_qrcode.setFixedSize(220, 220)
        self.lbl_qrcode.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_card_layout.addWidget(self.lbl_qrcode)

        qr_caption = QtWidgets.QLabel("Inquadra con la fotocamera")
        qr_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_caption.setStyleSheet("font-size: 11px; font-weight: 600; color: #4b5563; margin-top: 4px;")
        qr_card_layout.addWidget(qr_caption)

        qr_container.addWidget(self.qr_card)
        layout.addLayout(qr_container)

        # Barra URL & Selezione Interfaccia di Rete
        url_box = QtWidgets.QFrame()
        url_box.setObjectName("urlContainer")
        url_box_layout = QtWidgets.QHBoxLayout(url_box)
        url_box_layout.setContentsMargins(10, 8, 10, 8)
        url_box_layout.setSpacing(8)

        # Dropdown per selezionare l'interfaccia LAN (Wi-Fi, Ethernet, ecc.)
        self.combo_iface = QtWidgets.QComboBox()
        self.combo_iface.setObjectName("ifaceCombo")
        self.combo_iface.setToolTip("Seleziona l'interfaccia di rete LAN da mostrare nel QR Code")
        for iface_name, ip in self.interfaces:
            self.combo_iface.addItem(f"{iface_name} ({ip})", ip)
        self.combo_iface.currentIndexChanged.connect(self._on_interface_changed)
        url_box_layout.addWidget(self.combo_iface)

        # Campo URL visualizzabile e selezionabile
        self.txt_url = QtWidgets.QLineEdit()
        self.txt_url.setReadOnly(True)
        self.txt_url.setObjectName("urlLineEdit")
        url_box_layout.addWidget(self.txt_url, stretch=1)

        # Pulsante Copia Link
        self.btn_copy_url = QtWidgets.QPushButton("📋 Copia")
        self.btn_copy_url.setObjectName("btnAction")
        self.btn_copy_url.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_copy_url.clicked.connect(self._copy_url)
        url_box_layout.addWidget(self.btn_copy_url)

        # Pulsante Apri nel browser
        self.btn_open_browser = QtWidgets.QPushButton("🌐 Apri")
        self.btn_open_browser.setObjectName("btnAction")
        self.btn_open_browser.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_open_browser.clicked.connect(self._open_in_browser)
        url_box_layout.addWidget(self.btn_open_browser)

        layout.addWidget(url_box)

        # Pulsanti di controllo principali
        ctrl_layout = QtWidgets.QHBoxLayout()
        ctrl_layout.setSpacing(12)

        self.btn_stop_share = QtWidgets.QPushButton("⏹️ Ferma Condivisione")
        self.btn_stop_share.setObjectName("btnStop")
        self.btn_stop_share.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_stop_share.clicked.connect(self.stop_sharing)
        ctrl_layout.addWidget(self.btn_stop_share)

        self.btn_new_share = QtWidgets.QPushButton("🔄 Condividi Altro")
        self.btn_new_share.setObjectName("btnNew")
        self.btn_new_share.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_new_share.clicked.connect(self._select_another_file)
        ctrl_layout.addWidget(self.btn_new_share)

        layout.addLayout(ctrl_layout)

    # -------------------------------------------------------------
    # LOGICA DI CONDIVISIONE ED EVENTI
    # -------------------------------------------------------------

    def start_sharing(self, path: str):
        """Avvia la condivisione per il file o la cartella selezionata."""
        if not os.path.exists(path):
            QtWidgets.QMessageBox.warning(self, "Errore", "Il percorso selezionato non esiste.")
            return

        # Arresta eventuale server precedente
        self.stop_sharing(switch_view=False)

        is_folder = os.path.isdir(path)
        actual_path = path
        display_name = os.path.basename(os.path.abspath(path))

        # Se è una cartella, crea un archivio zip temporaneo per permettere
        # il download semplice in un singolo file da smartphone
        if is_folder:
            display_name = f"{display_name}.zip" if display_name else "cartella_condivisa.zip"
            self._append_log(f"Comprimo la cartella in archivio ZIP per il download: {path}", "info")
            try:
                temp_dir = tempfile.mkdtemp(prefix="localdrop_")
                zip_path = os.path.join(temp_dir, display_name)
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, _, files in os.walk(path):
                        for file in files:
                            full_file = os.path.join(root, file)
                            rel_file = os.path.relpath(full_file, path)
                            zipf.write(full_file, rel_file)
                actual_path = zip_path
                self.active_temp_zip = zip_path
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Errore Creazione ZIP", f"Impossibile comprimere la cartella:\n{e}")
                return

        file_size = os.path.getsize(actual_path)
        self.active_file_path = actual_path
        self.current_port = NetworkUtils.find_free_port()

        # Aggiorna la UI con i dati del file
        self.lbl_file_name.setText(display_name)
        size_str = self._format_size(file_size)
        self.lbl_file_details.setText(f"Dimensione: {size_str} • Percorso: {path}")
        self.file_icon_label.setText("📁" if is_folder else "📄")
        self.badge_type.setText("CARTELLA (ZIP)" if is_folder else "FILE")

        # Avvia il server nel thread separato
        self.server_thread = ShareServerThread(
            file_path=actual_path,
            display_name=display_name,
            is_folder=is_folder,
            file_size=file_size,
            port=self.current_port,
            signals=self.server_signals,
            parent=self
        )
        self.server_thread.start()

        # Aggiorna QR Code e URL
        self._refresh_url_and_qr()

        # Mostra la schermata di condivisione attiva
        self.stack.setCurrentIndex(1)

    def stop_sharing(self, switch_view: bool = True):
        """Arresta il server HTTP in background e ripulisce le risorse temporanee."""
        if self.server_thread:
            self._append_log("Arresto del server web locale in corso...", "info")
            self.server_thread.stop()
            self.server_thread = None

        # Ripulisci archivio ZIP temporaneo (se creato)
        if self.active_temp_zip and os.path.exists(self.active_temp_zip):
            try:
                temp_folder = os.path.dirname(self.active_temp_zip)
                os.remove(self.active_temp_zip)
                if os.path.isdir(temp_folder):
                    os.rmdir(temp_folder)
            except Exception:
                pass
            self.active_temp_zip = None

        self.active_file_path = None
        self.status_badge.setText("● INATTIVO")
        self.status_badge.setObjectName("statusBadgeInactive")
        self.status_badge.setStyleSheet("")  # forza ricaricamento stile

        if switch_view:
            self.stack.setCurrentIndex(0)
            self._append_log("Condivisione terminata. Server offline.", "info")

    def _select_another_file(self):
        """Apre un dialogo per selezionare un nuovo file da condividere."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Seleziona nuovo file")
        if path:
            self.start_sharing(path)

    def _refresh_url_and_qr(self):
        """Genera e aggiorna la stringa URL e il QR Code visualizzato."""
        if not self.active_file_path:
            return

        filename = os.path.basename(self.active_file_path)
        encoded_filename = urllib.parse.quote(filename)
        url = f"http://{self.current_ip}:{self.current_port}/{encoded_filename}"

        self.txt_url.setText(url)
        pixmap = QRCodeGenerator.generate_pixmap(url, size=220)
        self.lbl_qrcode.setPixmap(pixmap)

    def _on_interface_changed(self, index: int):
        """Aggiorna l'IP visualizzato e rigenera il QR Code al cambio di scheda di rete."""
        new_ip = self.combo_iface.itemData(index)
        if new_ip and new_ip != self.current_ip:
            self.current_ip = new_ip
            self._refresh_url_and_qr()
            self._append_log(f"Interfaccia selezionata: {new_ip}", "info")

    def _copy_url(self):
        """Copia l'URL negli appunti di sistema con feedback temporaneo sul pulsante."""
        url = self.txt_url.text()
        if url:
            clipboard = QtGui.QGuiApplication.clipboard()
            clipboard.setText(url)
            self.btn_copy_url.setText("✓ Copiato!")
            QtCore.QTimer.singleShot(1800, lambda: self.btn_copy_url.setText("📋 Copia"))

    def _open_in_browser(self):
        """Apre l'URL nel browser web predefinito di sistema."""
        url = self.txt_url.text()
        if url:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    def _on_server_started(self, ip: str, port: int):
        self.status_badge.setText(f"● SERVER ONLINE :{port}")
        self.status_badge.setObjectName("statusBadgeActive")
        self.status_badge.setStyleSheet("")  # forza ricaricamento stile
        self._append_log(f"Server web attivo su porta {port}. In ascolto su tutta la LAN.", "info")

    def _on_server_stopped(self):
        self.status_badge.setText("● INATTIVO")
        self.status_badge.setObjectName("statusBadgeInactive")
        self.status_badge.setStyleSheet("")

    def _on_server_error(self, err: str):
        self._append_log(f"Errore server HTTP: {err}", "error")
        QtWidgets.QMessageBox.critical(self, "Errore Server", f"Errore nel server di condivisione:\n{err}")

    def _on_transfer_completed(self, client_ip: str, filename: str, bytes_sent: int):
        pass

    def _append_log(self, message: str, level: str = "info"):
        """Aggiunge una riga formattata e timestampata alla console di log."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        color = "#93c5fd"  # azzurro chiaro per default
        if level == "download":
            color = "#4ade80"  # verde brillante
        elif level == "warn":
            color = "#fbbf24"  # giallo ambra
        elif level == "error":
            color = "#f87171"  # rosso corallo

        formatted_html = f'<span style="color: #64748b;">[{timestamp}]</span> <span style="color: {color};">{message}</span>'
        self.log_text.append(formatted_html)
        # Scroll automatico verso l'ultima riga
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_logs(self):
        self.log_text.clear()

    def _format_size(self, size_bytes: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

    def closeEvent(self, event: QtGui.QCloseEvent):
        """Arresta pulitamente il server e i file temporanei alla chiusura della finestra."""
        self.stop_sharing(switch_view=False)
        event.accept()

    # -------------------------------------------------------------
    # STILE DARK PROFESSIONALE (QSS)
    # -------------------------------------------------------------
    def _apply_global_theme(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0f1117;
            }
            QWidget {
                color: #e2e8f0;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            }
            #statusBadgeInactive {
                background-color: #1e2230;
                color: #94a3b8;
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 700;
            }
            #statusBadgeActive {
                background-color: rgba(16, 185, 129, 0.15);
                color: #10b981;
                border: 1px solid #10b981;
                border-radius: 12px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 700;
            }
            #infoCard {
                background-color: #161922;
                border: 1px solid #282f42;
                border-radius: 12px;
            }
            #typeBadge {
                background-color: rgba(0, 210, 255, 0.12);
                color: #00d2ff;
                border: 1px solid rgba(0, 210, 255, 0.25);
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 700;
            }
            #qrCard {
                background-color: #ffffff;
                border: 2px solid #e2e8f0;
                border-radius: 16px;
            }
            #urlContainer {
                background-color: #161922;
                border: 1px solid #282f42;
                border-radius: 10px;
            }
            #urlLineEdit {
                background-color: #0b0d12;
                border: 1px solid #23293a;
                border-radius: 6px;
                color: #38bdf8;
                font-family: "JetBrains Mono", "Fira Code", "Courier New", monospace;
                font-size: 13px;
                padding: 7px 12px;
            }
            #ifaceCombo {
                background-color: #232838;
                color: #f1f5f9;
                border: 1px solid #374151;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
                font-weight: 600;
            }
            #ifaceCombo::drop-down {
                border: none;
            }
            #btnAction {
                background-color: #232838;
                color: #e2e8f0;
                border: 1px solid #3b4256;
                border-radius: 6px;
                padding: 7px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            #btnAction:hover {
                background-color: #2d3449;
                border-color: #00d2ff;
                color: #ffffff;
            }
            #btnStop {
                background-color: #ef4444;
                color: #ffffff;
                border: none;
                border-radius: 10px;
                padding: 12px 20px;
                font-size: 14px;
                font-weight: 700;
            }
            #btnStop:hover {
                background-color: #dc2626;
            }
            #btnNew {
                background-color: #232838;
                color: #e2e8f0;
                border: 1px solid #374151;
                border-radius: 10px;
                padding: 12px 20px;
                font-size: 14px;
                font-weight: 600;
            }
            #btnNew:hover {
                background-color: #2d3449;
                border-color: #94a3b8;
                color: #ffffff;
            }
            #terminalLog {
                background-color: #0b0c10;
                border: 1px solid #1e2230;
                border-radius: 8px;
                color: #cbd5e1;
                font-family: "JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace;
                font-size: 12px;
                padding: 8px;
            }
            #btnClearLog {
                background-color: transparent;
                color: #64748b;
                border: none;
                font-size: 11px;
                font-weight: 600;
                padding: 2px 6px;
            }
            #btnClearLog:hover {
                color: #94a3b8;
            }
        """)


# ==============================================================================
# 6. ENTRY POINT APPLICAZIONE
# ==============================================================================

def main():
    # Supporto High DPI display (comune su laptop e schermi moderni)
    if hasattr(QtCore.Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(QtCore.Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Local Drop & Share")
    app.setApplicationDisplayName("Local Drop & Share")

    # Icona applicazione (se presente)
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QtGui.QIcon(icon_path))

    window = MainWindow()

    # Se viene passato un file da riga di comando (o dal file manager con 'Apri con')
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        QtCore.QTimer.singleShot(200, lambda: window.start_sharing(sys.argv[1]))

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
