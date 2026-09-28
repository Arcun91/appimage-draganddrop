#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script per generare l'icona moderna ad alta risoluzione (PNG) per Local Drop & Share.
"""

import os
from PIL import Image, ImageDraw


def generate_icon(output_path: str = "assets/icon.png", size: int = 256):
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Squircle di sfondo scuro
    pad = 12
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=48,
        fill='#161922',
        outline='#00d2ff',
        width=6
    )

    # Cerchio centrale luminoso
    center = (size // 2, size // 2 - 10)
    draw.ellipse(
        [center[0] - 65, center[1] - 65, center[0] + 65, center[1] + 65],
        fill='#1f2432',
        outline='#00b4d8',
        width=3
    )

    # Onde di trasmissione Wi-Fi / LAN
    draw.arc(
        [center[0] - 85, center[1] - 85, center[0] + 85, center[1] + 85],
        start=210, end=330,
        fill='#00d2ff',
        width=5
    )
    draw.arc(
        [center[0] - 105, center[1] - 105, center[0] + 105, center[1] + 105],
        start=215, end=325,
        fill='#38bdf8',
        width=4
    )

    # Freccia centrale verso il basso (Drop / Download)
    cx, cy = center[0], center[1] - 5
    arrow_color = '#ffffff'
    draw.rectangle([cx - 8, cy - 30, cx + 8, cy + 15], fill=arrow_color)
    draw.polygon([(cx - 28, cy + 10), (cx + 28, cy + 10), (cx, cy + 38)], fill=arrow_color)

    # Vassoio di ricezione alla base
    draw.rounded_rectangle([cx - 40, cy + 46, cx + 40, cy + 54], radius=4, fill='#00d2ff')

    # Badge con indicatori LED
    badge_w = 64
    badge_h = 24
    bx = (size - badge_w) // 2
    by = size - pad - 36
    draw.rounded_rectangle([bx, by, bx + badge_w, by + badge_h], radius=6, fill='#00d2ff')
    draw.ellipse([bx + 12, by + 7, bx + 22, by + 17], fill='#161922')
    draw.ellipse([bx + 27, by + 7, bx + 37, by + 17], fill='#161922')
    draw.ellipse([bx + 42, by + 7, bx + 52, by + 17], fill='#161922')

    img.save(output_path, 'PNG')
    print(f"Icona salvata con successo in: {output_path}")


if __name__ == "__main__":
    generate_icon()
