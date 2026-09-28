#!/usr/bin/env bash
# ==============================================================================
# Build Script: Local Drop & Share -> Linux AppImage
# ==============================================================================
# Questo script compila l'applicazione con PyInstaller e la impacchetta
# in un AppImage autonomo e portabile per Linux x86_64.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="Local_Drop_Share"
BIN_NAME="local_drop_share"
APP_DIR="$SCRIPT_DIR/build/AppDir"
DIST_DIR="$SCRIPT_DIR/dist"

echo "======================================================"
echo " [1/5] Verifica dipendenze e preparazione ambiente..."
echo "======================================================"

python3 -m pip show pyinstaller > /dev/null 2>&1 || {
    echo ">> Installazione di PyInstaller..."
    python3 -m pip install --user --break-system-packages pyinstaller
}

python3 -m pip show qrcode > /dev/null 2>&1 || {
    echo ">> Installazione di qrcode..."
    python3 -m pip install --user --break-system-packages qrcode
}

# Generazione icona ad alta risoluzione
if [ ! -f "assets/icon.png" ]; then
    echo ">> Generazione icona applicazione..."
    python3 generate_icon.py
fi

echo "======================================================"
echo " [2/5] Compilazione con PyInstaller (onedir mode)..."
echo "======================================================"

# Pulizia build precedenti
rm -rf "$SCRIPT_DIR/build/local_drop_share"
rm -rf "$DIST_DIR/local_drop_share"
rm -rf "$APP_DIR"

pyinstaller --noconfirm --clean \
    --name "$BIN_NAME" \
    --onedir \
    --windowed \
    --add-data "assets/icon.png:assets" \
    local_drop_share.py

echo "======================================================"
echo " [3/5] Creazione struttura AppDir per AppImage..."
echo "======================================================"

mkdir -p "$APP_DIR/usr/bin"
mkdir -p "$APP_DIR/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$APP_DIR/usr/share/applications"

# Copia l'output compilato da PyInstaller in AppDir/usr/bin/
cp -r "$DIST_DIR/$BIN_NAME" "$APP_DIR/usr/bin/"

# Copia l'icona
cp "assets/icon.png" "$APP_DIR/local-drop-share.png"
cp "assets/icon.png" "$APP_DIR/usr/share/icons/hicolor/256x256/apps/local-drop-share.png"
cp "assets/icon.png" "$APP_DIR/.DirIcon"

# Creazione Desktop Entry
cat << 'EOF' > "$APP_DIR/local-drop-share.desktop"
[Desktop Entry]
Type=Application
Name=Local Drop & Share
Comment=Condividi file o cartelle istantaneamente sulla rete Wi-Fi/LAN locale con un QR code
Exec=local_drop_share %U
Icon=local-drop-share
Terminal=false
Categories=Network;FileTransfer;Utility;
Keywords=share;drop;wifi;lan;qr;transfer;
MimeType=application/octet-stream;inode/directory;
StartupNotify=true
EOF

cp "$APP_DIR/local-drop-share.desktop" "$APP_DIR/usr/share/applications/"

# Creazione dello script di avvio AppRun
cat << 'EOF' > "$APP_DIR/AppRun"
#!/bin/sh
set -e
HERE="$(dirname "$(readlink -f "${0}")")"
export PATH="${HERE}/usr/bin:${HERE}/usr/bin/local_drop_share:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/bin/local_drop_share:${LD_LIBRARY_PATH}"
exec "${HERE}/usr/bin/local_drop_share/local_drop_share" "$@"
EOF

chmod +x "$APP_DIR/AppRun"

echo "======================================================"
echo " [4/5] Acquisizione appimagetool..."
echo "======================================================"

APPIMAGETOOL=""
if command -v appimagetool > /dev/null 2>&1; then
    APPIMAGETOOL="$(command -v appimagetool)"
elif [ -f "$SCRIPT_DIR/appimagetool-x86_64.AppImage" ]; then
    APPIMAGETOOL="$SCRIPT_DIR/appimagetool-x86_64.AppImage"
else
    echo ">> Download di appimagetool-x86_64.AppImage..."
    curl -L -o "$SCRIPT_DIR/appimagetool-x86_64.AppImage" \
        "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$SCRIPT_DIR/appimagetool-x86_64.AppImage"
    APPIMAGETOOL="$SCRIPT_DIR/appimagetool-x86_64.AppImage"
fi

echo "======================================================"
echo " [5/5] Generazione file AppImage..."
echo "======================================================"

OUTPUT_APPIMAGE="${APP_NAME}-x86_64.AppImage"
rm -f "$OUTPUT_APPIMAGE"

# ARCH=x86_64 per appimagetool
export ARCH=x86_64

# Utilizzo di --appimage-extract-and-run nel caso appimagetool stesso giri in container/senza FUSE nativo
if [ -n "$APPIMAGETOOL" ]; then
    "$APPIMAGETOOL" --appimage-extract-and-run "$APP_DIR" "$OUTPUT_APPIMAGE" || \
    "$APPIMAGETOOL" "$APP_DIR" "$OUTPUT_APPIMAGE"
fi

if [ -f "$OUTPUT_APPIMAGE" ]; then
    chmod +x "$OUTPUT_APPIMAGE"
    echo ""
    echo "======================================================"
    echo " SUCCESS! AppImage generato con successo:"
    echo " -> $SCRIPT_DIR/$OUTPUT_APPIMAGE"
    ls -lh "$OUTPUT_APPIMAGE"
    echo "======================================================"
else
    echo "ERRORE: Generazione AppImage non riuscita."
    exit 1
fi
