"""
generate_quanttix_logo.py — gera assets PNG da logo Quanttix usada no
boleto fictício do handoff_server.

Reproduz visualmente o componente Header.tsx do quanttix_frontend
(div com gradient laranja→azul + ícone TrendingUp + texto "Quanttix").

Saída:
  handoff_server/assets/quanttix_logo.png         600x150  (topo do boleto)
  handoff_server/assets/quanttix_logo_compact.png 200x80   (ficha de compensação,
                                                            substitui "Banco do Brasil")

Uso (rodar uma vez, commitar os PNGs no repo):
  cd quanttix-negotiation
  python3 scripts/generate_quanttix_logo.py

Dependência: Pillow >= 9.x (já vem em qualquer ambiente Python moderno).
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# ── Cores Tailwind do Header.tsx ────────────────────────────────────────
ORANGE_500 = (249, 115, 22)   # #f97316
BLUE_500 = (59, 130, 246)     # #3b82f6
WHITE = (255, 255, 255)
GRAY_400 = (156, 163, 175)


def _gradient_box(
    draw: ImageDraw.ImageDraw,
    *,
    xy: tuple[int, int, int, int],
    color_left: tuple[int, int, int],
    color_right: tuple[int, int, int],
    radius: int,
) -> None:
    """Desenha um retângulo arredondado com gradient horizontal."""
    x0, y0, x1, y1 = xy
    width = x1 - x0
    for i in range(width):
        ratio = i / max(1, width - 1)
        r = int(color_left[0] + (color_right[0] - color_left[0]) * ratio)
        g = int(color_left[1] + (color_right[1] - color_left[1]) * ratio)
        b = int(color_left[2] + (color_right[2] - color_left[2]) * ratio)
        draw.line([(x0 + i, y0), (x0 + i, y1)], fill=(r, g, b))
    # Mascara nos cantos para virar rounded — preto fora, transparente dentro
    if radius > 0:
        mask = Image.new("L", (x1 - x0, y1 - y0), 0)
        mdraw = ImageDraw.Draw(mask)
        mdraw.rounded_rectangle(
            (0, 0, x1 - x0 - 1, y1 - y0 - 1), radius=radius, fill=255
        )
        # nao podemos aplicar mask aqui diretamente — devolvemos a mask
        # via atributo (hack) ou desenhamos sobre fundo branco/transparente.
        # Para simplificar, vamos desenhar o retangulo pixel a pixel acima
        # e depois aplicar a mask no painel final na funcao chamadora.


def _draw_trending_up_icon(
    draw: ImageDraw.ImageDraw,
    *,
    cx: int,
    cy: int,
    size: int,
    color: tuple[int, int, int],
    line_w: int,
) -> None:
    """
    Desenha o icone TrendingUp (lucide-react) centrado em (cx, cy).

    SVG path original:
      polyline 22 7  13.5 15.5  8.5 10.5  2 17
      polyline 16 7  22 7  22 13

    Coordenadas no viewBox 24x24 — escalamos para `size`.
    """
    s = size / 24.0
    pts_main = [(22, 7), (13.5, 15.5), (8.5, 10.5), (2, 17)]
    pts_arrow = [(16, 7), (22, 7), (22, 13)]

    def remap(p):
        return (cx + (p[0] - 12) * s, cy + (p[1] - 12) * s)

    # Polilinha principal (a "linha de tendência")
    pts_main_px = [remap(p) for p in pts_main]
    for i in range(len(pts_main_px) - 1):
        draw.line(
            [pts_main_px[i], pts_main_px[i + 1]],
            fill=color, width=line_w,
        )
    # Polilinha do canto da seta
    pts_arrow_px = [remap(p) for p in pts_arrow]
    for i in range(len(pts_arrow_px) - 1):
        draw.line(
            [pts_arrow_px[i], pts_arrow_px[i + 1]],
            fill=color, width=line_w,
        )


def _resolve_font(name_candidates: list[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """
    Tenta carregar uma das fontes candidatas; cai para default do PIL.
    """
    common_paths = [
        "/System/Library/Fonts/Supplemental",
        "/System/Library/Fonts",
        "/Library/Fonts",
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/truetype/liberation",
        "/usr/share/fonts/TTF",
    ]
    for base in common_paths:
        for name in name_candidates:
            p = Path(base) / name
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), size=size)
                except Exception:
                    continue
    return ImageFont.load_default()


def gerar_logo_principal(out_path: Path) -> None:
    """
    600x150: bloco gradient com TrendingUp + textos "Quanttix" e tagline.
    Mesma proporção visual do Header.tsx (logo box + textos à direita).
    """
    W, H = 600, 150
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Caixa do gradient (canto esquerdo, quadrada)
    box_size = 100
    box_padding = 25
    box_xy = (box_padding, box_padding, box_padding + box_size, box_padding + box_size)

    # Cria layer separada do gradient com a mask rounded
    grad_layer = Image.new("RGB", (box_size, box_size), WHITE)
    gdraw = ImageDraw.Draw(grad_layer)
    for i in range(box_size):
        ratio = i / max(1, box_size - 1)
        r = int(ORANGE_500[0] + (BLUE_500[0] - ORANGE_500[0]) * ratio)
        g = int(ORANGE_500[1] + (BLUE_500[1] - ORANGE_500[1]) * ratio)
        b = int(ORANGE_500[2] + (BLUE_500[2] - ORANGE_500[2]) * ratio)
        gdraw.line([(i, 0), (i, box_size - 1)], fill=(r, g, b))
    # Icone TrendingUp branco centrado na caixa
    icon_draw = ImageDraw.Draw(grad_layer)
    _draw_trending_up_icon(
        icon_draw,
        cx=box_size // 2, cy=box_size // 2,
        size=int(box_size * 0.5), color=WHITE, line_w=6,
    )
    # Mascara rounded
    mask = Image.new("L", (box_size, box_size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, box_size - 1, box_size - 1), radius=16, fill=255,
    )
    img.paste(grad_layer, (box_xy[0], box_xy[1]), mask=mask)

    # Texto à direita
    title_font = _resolve_font(
        ["Helvetica.ttc", "HelveticaNeue.ttc", "Arial.ttf", "DejaVuSans-Bold.ttf"],
        size=48,
    )
    sub_font = _resolve_font(
        ["Helvetica.ttc", "Arial.ttf", "DejaVuSans.ttf"], size=18,
    )
    text_x = box_xy[2] + 20
    draw.text((text_x, 35), "Quanttix", fill=WHITE, font=title_font)
    draw.text((text_x, 95), "Treasury Management AI", fill=GRAY_400, font=sub_font)

    # Fundo escuro (mesmo tom do Header.tsx: gray-900 com alpha)
    bg = Image.new("RGBA", (W, H), (17, 24, 39, 255))   # gray-900
    bg.alpha_composite(img)
    bg = bg.convert("RGB")
    bg.save(out_path, "PNG", optimize=True)
    print(f"OK {out_path} ({W}x{H})")


def gerar_logo_compact(out_path: Path) -> None:
    """
    200x80: versão para substituir 'Banco do Brasil' na ficha de compensação.
    Mostra apenas "Quanttix Tech" estilizado no fundo gradient (sem ícone)
    para caber no espaço pequeno da ficha.
    """
    W, H = 200, 80
    img = Image.new("RGB", (W, H), WHITE)
    # Gradient cobrindo tudo
    draw = ImageDraw.Draw(img)
    for i in range(W):
        ratio = i / max(1, W - 1)
        r = int(ORANGE_500[0] + (BLUE_500[0] - ORANGE_500[0]) * ratio)
        g = int(ORANGE_500[1] + (BLUE_500[1] - ORANGE_500[1]) * ratio)
        b = int(ORANGE_500[2] + (BLUE_500[2] - ORANGE_500[2]) * ratio)
        draw.line([(i, 0), (i, H - 1)], fill=(r, g, b))

    # Texto centralizado
    title_font = _resolve_font(
        ["Helvetica.ttc", "HelveticaNeue.ttc", "Arial.ttf", "DejaVuSans-Bold.ttf"],
        size=22,
    )
    sub_font = _resolve_font(
        ["Helvetica.ttc", "Arial.ttf", "DejaVuSans.ttf"], size=12,
    )

    # Centraliza o texto principal
    txt_main = "Quanttix"
    txt_sub = "Tech"
    bbox_main = draw.textbbox((0, 0), txt_main, font=title_font)
    bbox_sub = draw.textbbox((0, 0), txt_sub, font=sub_font)
    w_main = bbox_main[2] - bbox_main[0]
    w_sub = bbox_sub[2] - bbox_sub[0]

    draw.text(((W - w_main) // 2, 15), txt_main, fill=WHITE, font=title_font)
    draw.text(((W - w_sub) // 2, 48), txt_sub, fill=WHITE, font=sub_font)

    img.save(out_path, "PNG", optimize=True)
    print(f"OK {out_path} ({W}x{H})")


if __name__ == "__main__":
    here = Path(__file__).resolve().parent.parent
    assets = here / "handoff_server" / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    gerar_logo_principal(assets / "quanttix_logo.png")
    gerar_logo_compact(assets / "quanttix_logo_compact.png")

    print()
    print(f"Assets gerados em: {assets}")
