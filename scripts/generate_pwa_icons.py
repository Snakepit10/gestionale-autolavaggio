"""Genera icone PWA placeholder per il gestionale autolavaggio.

Crea 8 PNG nelle dimensioni richieste dal manifest.json + apple-touch-icon
+ favicon.ico + 2 shortcut icons. Sfondo blu primario, lettera "A" bianca
centrata, bordi arrotondati per look "maskable".

Eseguire una sola volta: python scripts/generate_pwa_icons.py
"""
import os
from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICONS_DIR = os.path.join(BASE, 'static', 'icons')
STATIC_DIR = os.path.join(BASE, 'static')
os.makedirs(ICONS_DIR, exist_ok=True)

BG = (13, 110, 253)       # #0d6efd primary
FG = (255, 255, 255)
SIZES = [72, 96, 128, 144, 152, 192, 384, 512]


def font_for(size):
    # Prova alcuni font di sistema; fallback a default
    candidates = [
        'C:/Windows/Fonts/arialbd.ttf',
        'C:/Windows/Fonts/seguibl.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, int(size * 0.55))
            except Exception:
                pass
    return ImageFont.load_default()


def make_icon(size, letter='A', radius_ratio=0.18):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = int(size * radius_ratio)
    # Rounded rect background
    draw.rounded_rectangle([(0, 0), (size, size)], radius=radius, fill=BG)
    # Letter centrata
    fnt = font_for(size)
    bbox = draw.textbbox((0, 0), letter, font=fnt)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - w) // 2 - bbox[0]
    y = (size - h) // 2 - bbox[1] - int(size * 0.02)
    draw.text((x, y), letter, font=fnt, fill=FG)
    return img


def main():
    # Set di icone principali
    for s in SIZES:
        path = os.path.join(ICONS_DIR, f'icon-{s}x{s}.png')
        make_icon(s).save(path, 'PNG')
        print('Generato', path)

    # Apple touch icon (180x180)
    make_icon(180).save(os.path.join(ICONS_DIR, 'apple-touch-icon.png'), 'PNG')
    print('Generato apple-touch-icon.png')

    # Shortcuts (96x96, lettera diversa)
    make_icon(96, letter='+').save(os.path.join(ICONS_DIR, 'shortcut-order.png'), 'PNG')
    make_icon(96, letter='Q').save(os.path.join(ICONS_DIR, 'shortcut-scanner.png'), 'PNG')
    print('Generati shortcuts')

    # Favicon (multi-size ICO)
    fav = make_icon(64).resize((64, 64), Image.LANCZOS)
    fav32 = fav.resize((32, 32), Image.LANCZOS)
    fav16 = fav.resize((16, 16), Image.LANCZOS)
    fav.save(os.path.join(STATIC_DIR, 'favicon.ico'),
             format='ICO', sizes=[(16, 16), (32, 32), (64, 64)])
    print('Generato favicon.ico')


if __name__ == '__main__':
    main()
