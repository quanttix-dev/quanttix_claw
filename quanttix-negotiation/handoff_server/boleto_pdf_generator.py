"""
boleto_pdf_generator — gera PDF fictício de boleto Quanttix Tech para
quando o backend retorna `boleto=None` no `accept_and_issue` (ambiente
de teste sem Protheus real e antes do DataEng entrar).

O layout espelha o modelo bancário tradicional (referência: boleto
Canaã anexado pelo usuário) substituindo:
  - Empresa: CANAA → QUANTTIX TECH LTDA (CNPJ fictício 55.444.333/0001-00)
  - Banco: BANCO DO BRASIL → Quanttix Tech (código 999)
  - Tabela "O QUE FOI CONTRATADO" → texto narrativo "Renegociação de
    título em aberto" com valores acordados

Marca d'água "SIMULAÇÃO" diagonal em cinza claro deixa explícito que
não é boleto real (sem possibilidade de pagamento).

Constantes da Quanttix Tech mock ficam em `MOCK_CEDENTE`. Quando o
DataEng entrar (vendor=AIRFLOW_SIM), o backend devolve dados reais e
esse gerador deixa de ser chamado.
"""

import logging
import random
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from fpdf import FPDF

logger = logging.getLogger(__name__)

# ── Assets ──────────────────────────────────────────────────────────────

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LOGO_PRINCIPAL = ASSETS_DIR / "quanttix_logo.png"
LOGO_COMPACT = ASSETS_DIR / "quanttix_logo_compact.png"

# ── Identidade fictícia Quanttix Tech (cedente) ─────────────────────────
# Sincronizado com memory/project_quanttix_tech_mock_identity.md

@dataclass(frozen=True)
class CedenteMock:
    razao_social: str = "QUANTTIX TECH LTDA"
    cnpj: str = "55.444.333/0001-00"
    endereco: str = "Av. Brig. Faria Lima, 4391 - Conj. 1812, 18o andar - Itaim Bibi"
    cidade_uf: str = "São Paulo / SP"
    cep: str = "04538-133"
    telefone: str = "(11) 4002-8922"
    email: str = "financeiro@quanttix.tech"
    # Bancário fictício (banco 999 = simulação; demais com 9s respeitando tamanho)
    banco_codigo: str = "999"
    banco_nome: str = "Quanttix Tech"
    agencia: str = "9999"
    conta: str = "99999-9"
    carteira: str = "9"
    especie_documento: str = "DM"
    aceite: str = "N"


MOCK_CEDENTE = CedenteMock()

# ── Input do gerador ────────────────────────────────────────────────────


@dataclass
class BoletoFicticioInput:
    """Dados necessários para montar o PDF do boleto fictício."""

    # Identificadores do título / negociação
    title_id_erp: str            # ex "QTX20260311VNF"
    valor_nominal: Decimal       # valor original (sem desconto)
    desconto_pct: Decimal        # ex Decimal("1.0") para 1%
    valor_final: Decimal         # valor a pagar (valor_nominal - desconto)
    dt_emissao: date
    dt_vencimento: date

    # Identificação da contraparte (do binding Redis)
    counterpart_nome: str
    counterpart_doc: str

    # Identificadores CNAB (gerados antes — ver gerar_identificadores_cnab)
    nosso_numero: str
    linha_digitavel: str
    codigo_barras: str

    # Cedente fixo (defaults da MOCK_CEDENTE)
    cedente: CedenteMock = field(default_factory=lambda: MOCK_CEDENTE)


# ── Helpers ─────────────────────────────────────────────────────────────


def _fmt_brl(value) -> str:
    try:
        f = float(value)
        return (
            f"R$ {f:,.2f}"
            .replace(",", "X").replace(".", ",").replace("X", ".")
        )
    except (ValueError, TypeError):
        return f"R$ {value}"


def _fmt_data(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def gerar_identificadores_cnab(
    *,
    seq_global: int,
    valor_final: Decimal,
    dt_vencimento: date,
    banco_portador: str = "999",
    agencia: str = "9999",
) -> tuple[str, str, str]:
    """
    Gera (nosso_numero, linha_digitavel, codigo_barras) para o boleto
    fictício. Algoritmo espelha sim_gerar_boleto.py do dataeng para
    manter consistência quando o DataEng entrar.
    """
    nosso_numero = f"{banco_portador}{agencia}{str(seq_global).zfill(10)}"
    valor_int = int(round(float(valor_final) * 100))
    linha_digitavel = (
        f"{banco_portador}{agencia}00001 "
        f"{nosso_numero[:5]}0 "
        f"{nosso_numero[5:]}0 "
        f"1 {dt_vencimento.strftime('%Y%m%d')}{str(valor_int).zfill(10)}"
    )
    codigo_barras = nosso_numero + str(valor_int).zfill(10)
    return nosso_numero, linha_digitavel, codigo_barras


# ── PDF class ───────────────────────────────────────────────────────────


class _BoletoPDF(FPDF):
    """
    Geração do PDF — posicionamento manual via set_xy + cell para
    reproduzir o layout bancário tradicional.
    """

    MARGIN_LR = 15

    def __init__(self, data: BoletoFicticioInput) -> None:
        super().__init__(format="A4", unit="mm")
        self.data = data
        self.set_auto_page_break(auto=False)
        self.set_margins(self.MARGIN_LR, 12, self.MARGIN_LR)

    def header(self):
        pass

    def render(self) -> None:
        self.add_page()
        self._draw_topo()
        self._draw_destinatario(y=63)
        self._draw_narrativa_renegociacao(y=82)
        self._draw_aviso_recibo(y=140)
        self._draw_linha_corte(y=158)
        self._draw_ficha_compensacao(y=162)
        self._draw_marca_dagua()

    # ── Topo: logo + dados empresa + box datas ─────────────────────────

    def _draw_topo(self) -> None:
        # Logo principal Quanttix à esquerda
        if LOGO_PRINCIPAL.exists():
            self.image(str(LOGO_PRINCIPAL), x=15, y=11, w=55, h=14)

        # Título à direita
        self.set_xy(120, 13)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(0, 0, 0)
        self.cell(75, 6, "Detalhamento da Negociação", align="R")

        # Linha horizontal separadora
        self.set_draw_color(180, 180, 180)
        self.line(15, 28, 195, 28)

        # Dados empresa à esquerda
        c = self.data.cedente
        self.set_xy(15, 31)
        self.set_font("Helvetica", "B", 9)
        self.cell(120, 4, c.razao_social)

        self.set_font("Helvetica", "", 8)
        self.set_text_color(60, 60, 60)
        infos = [
            f"CNPJ: {c.cnpj}",
            c.endereco,
            f"{c.cidade_uf} - CEP {c.cep}",
            f"Fone: {c.telefone}  /  {c.email}",
        ]
        for i, line in enumerate(infos):
            self.set_xy(15, 35 + i * 4)
            self.cell(120, 4, line)

        # Box "Emissão / Vencimento" à direita
        self.set_text_color(0, 0, 0)
        self.set_draw_color(150, 150, 150)
        self.rect(140, 31, 55, 16)
        self.set_xy(140, 31)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(80, 80, 80)
        self.cell(27.5, 5, "Emissão", align="C")
        self.cell(27.5, 5, "Vencimento", align="C")

        self.set_text_color(0, 0, 0)
        self.set_xy(140, 37)
        self.set_font("Helvetica", "B", 10)
        self.cell(27.5, 7, _fmt_data(self.data.dt_emissao), align="C")
        self.cell(27.5, 7, _fmt_data(self.data.dt_vencimento), align="C")

        # ID do título logo abaixo do box
        self.set_xy(140, 50)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(80, 80, 80)
        self.cell(55, 4, f"Título: {self.data.title_id_erp}", align="R")

    # ── Destinatário ───────────────────────────────────────────────────

    def _draw_destinatario(self, *, y: float) -> None:
        self.set_text_color(80, 80, 80)
        self.set_font("Helvetica", "", 8)
        self.set_xy(15, y)
        self.cell(40, 4, "Destinatário:")

        self.set_text_color(0, 0, 0)
        self.set_xy(15, y + 4)
        self.set_font("Helvetica", "B", 10)
        self.cell(180, 5, self.data.counterpart_nome)

        self.set_xy(15, y + 10)
        self.set_font("Helvetica", "", 8)
        self.cell(180, 4, f"CNPJ/CPF: {self.data.counterpart_doc}")

        self.set_xy(15, y + 14)
        self.set_text_color(80, 80, 80)
        self.cell(180, 4, "Endereço a confirmar diretamente com o beneficiário")

    # ── Bloco narrativo da renegociação ───────────────────────────────

    def _draw_narrativa_renegociacao(self, *, y: float) -> None:
        # Cabeçalho do bloco
        self.set_xy(15, y)
        self.set_font("Helvetica", "B", 11)
        self.set_fill_color(235, 235, 235)
        self.set_text_color(0, 0, 0)
        self.cell(180, 7, "RENEGOCIAÇÃO DE TÍTULO EM ABERTO",
                  fill=True, border=1, align="L")

        # Texto introdutório
        self.set_xy(15, y + 9)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(50, 50, 50)
        texto = (
            f"Este boleto refere-se à renegociação do título "
            f"{self.data.title_id_erp} em seu nome, acordada via canal "
            f"autenticado em {_fmt_data(self.data.dt_emissao)}."
        )
        self.multi_cell(180, 4.5, texto)

        # Linhas de valores
        yy = y + 20
        valor_desc = self.data.valor_nominal - self.data.valor_final
        self.set_text_color(0, 0, 0)

        self.set_xy(15, yy)
        self.set_font("Helvetica", "", 9)
        self.cell(130, 5, "Título original (valor nominal):")
        self.cell(50, 5, _fmt_brl(self.data.valor_nominal), align="R")

        self.set_xy(15, yy + 5)
        self.cell(130, 5,
                  f"Desconto concedido ({self.data.desconto_pct}%):")
        self.cell(50, 5, _fmt_brl(valor_desc), align="R")

        # Linha separadora
        self.set_draw_color(120, 120, 120)
        self.line(95, yy + 11.5, 195, yy + 11.5)

        # Valor a pagar (destaque)
        self.set_xy(15, yy + 13)
        self.set_font("Helvetica", "B", 12)
        self.cell(130, 6, "Valor a pagar (à vista):")
        self.set_text_color(0, 80, 0)   # verde discreto
        self.cell(50, 6, _fmt_brl(self.data.valor_final), align="R")
        self.set_text_color(0, 0, 0)

        # Texto de encerramento
        self.set_xy(15, yy + 22)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(80, 80, 80)
        self.multi_cell(180, 4, (
            "O pagamento dentro do prazo de validade deste boleto encerra "
            "formalmente a inadimplência referente ao título mencionado."
        ))

    # ── Aviso "validade" + box recibo ──────────────────────────────────

    def _draw_aviso_recibo(self, *, y: float) -> None:
        self.set_xy(15, y)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(100, 100, 100)
        self.multi_cell(125, 3, (
            "Este recibo somente terá validade com a autenticação mecânica "
            "ou acompanhado do recibo de pagamento emitido pelo Banco."
        ))

        self.set_draw_color(150, 150, 150)
        self.rect(140, y, 55, 10)
        self.set_xy(140, y + 1)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(80, 80, 80)
        self.cell(55, 4, "Autenticação Mecânica", align="C")
        self.set_xy(140, y + 4)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(0, 0, 0)
        self.cell(55, 4, "RECIBO DO PAGADOR", align="C")

    # ── Linha de corte ──────────────────────────────────────────────────

    def _draw_linha_corte(self, *, y: float) -> None:
        self.set_draw_color(180, 180, 180)
        self.set_line_width(0.2)
        x = 15
        while x < 195:
            self.line(x, y, x + 2, y)
            x += 4
        self.set_xy(95, y - 2)
        self.set_font("Helvetica", "", 6)
        self.set_text_color(150, 150, 150)
        self.cell(20, 3, "corte aqui", align="C")
        self.set_line_width(0.3)

    # ── Ficha de compensação ────────────────────────────────────────────

    def _draw_ficha_compensacao(self, *, y: float) -> None:
        c = self.data.cedente

        # Linha 1: [Logo compact] | código 999 | linha digitável
        if LOGO_COMPACT.exists():
            self.image(str(LOGO_COMPACT), x=15, y=y, w=30, h=10)

        # Código bancário
        self.set_xy(46, y + 2)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(0, 0, 0)
        self.set_draw_color(120, 120, 120)
        # Linhas verticais ao redor do código
        self.line(45, y, 45, y + 10)
        self.line(61, y, 61, y + 10)
        self.cell(15, 6, c.banco_codigo, align="C")

        # Linha digitável
        self.set_xy(63, y + 2)
        self.set_font("Helvetica", "B", 10)
        self.cell(132, 6, self.data.linha_digitavel, align="L")

        # Linha horizontal abaixo
        self.line(15, y + 10, 195, y + 10)

        # Linha 2: Local pagamento + Vencimento
        y2 = y + 12
        self._row_label_value(
            x=15, y=y2,
            left_label="Local de pagamento",
            left_value="PAGÁVEL EM QUALQUER BANCO OU LOTÉRICA ATÉ O VENCIMENTO",
            right_label="Vencimento",
            right_value=_fmt_data(self.data.dt_vencimento),
            right_x=160, right_w=35,
        )

        # Linha 3: Beneficiário + Agência/Código
        y3 = y2 + 9
        self._row_label_value(
            x=15, y=y3,
            left_label="Beneficiário",
            left_value=f"{c.razao_social}  -  CNPJ {c.cnpj}",
            right_label="Agência / Código",
            right_value=f"{c.agencia} / {c.conta}",
            right_x=160, right_w=35,
        )

        # Linha 4: Data Doc | Núm Doc | Espécie | Aceite | Data Proc | Nosso Nº
        y4 = y3 + 9
        cols4 = [
            ("Data Doc.", _fmt_data(self.data.dt_emissao), 22),
            ("Núm. Doc.", self.data.title_id_erp[:18], 32),
            ("Espécie", c.especie_documento, 18),
            ("Aceite", c.aceite, 15),
            ("Data Proc.", _fmt_data(self.data.dt_emissao), 22),
            ("Nosso Número", self.data.nosso_numero, 71),
        ]
        self._cols_row(x=15, y=y4, cols=cols4)

        # Linha 5: Uso Banco | Carteira | Espécie | Quant | (x) Valor | (=) Valor Doc
        y5 = y4 + 9
        cols5 = [
            ("Uso do Banco", "-", 30),
            ("Carteira", c.carteira, 22),
            ("Espécie Moeda", "R$", 25),
            ("Quant. Moeda", "-", 25),
            ("(X) Valor", "-", 28),
            ("(=) Valor Documento", _fmt_brl(self.data.valor_final), 50),
        ]
        self._cols_row(x=15, y=y5, cols=cols5, last_right=True, last_bold=True)

        # Linha 6: Instruções + (-)(+)(=) valores
        y6 = y5 + 9
        self.set_xy(15, y6)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(80, 80, 80)
        self.cell(125, 3, "Instruções (texto de responsabilidade do beneficiário)")

        self.set_xy(15, y6 + 3)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(0, 0, 0)
        self.multi_cell(125, 4, (
            "- Após o vencimento cobrar juros de 1% ao mês.\n"
            "- Após o vencimento cobrar multa de 2%.\n"
            "- Este boleto só pode ser pago até 30 dias após o vencimento.\n"
            "- Protesto em 45 dias a contar do vencimento."
        ))

        # Aviso adicional de simulação (destaque)
        self.set_xy(15, y6 + 22)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(180, 0, 0)
        self.cell(125, 4, "** BOLETO DE SIMULAÇÃO - NÃO PAGAR **")
        self.set_text_color(0, 0, 0)

        # Coluna direita: (-)(+)(=) valores
        x_right = 145
        labels_right = [
            "(-) Desconto / Abatimentos",
            "(-) Outras Deduções",
            "(+) Juros / Multa",
            "(+) Outros Acréscimos",
            "(=) Valor Cobrado",
        ]
        for i, label in enumerate(labels_right):
            ly = y6 + i * 5
            self.set_xy(x_right, ly)
            self.set_font("Helvetica", "", 7)
            self.set_text_color(80, 80, 80)
            self.cell(50, 3, label)
            self.set_xy(x_right, ly + 2)
            self.set_font("Helvetica", "", 8)
            self.set_text_color(0, 0, 0)
            # Linha separadora cinza abaixo
            self.cell(50, 3, "-", align="R")
            self.set_draw_color(200, 200, 200)
            self.line(x_right, ly + 5, x_right + 50, ly + 5)

        # Pagador
        y7 = y6 + 30
        self.set_xy(15, y7)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(80, 80, 80)
        self.cell(180, 3, "Pagador")

        self.set_xy(15, y7 + 3)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(0, 0, 0)
        self.cell(120, 5, self.data.counterpart_nome)
        self.set_xy(140, y7 + 3)
        self.cell(55, 5, self.data.counterpart_doc, align="R")

        self.set_xy(15, y7 + 8)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(80, 80, 80)
        self.cell(180, 4,
                  "Endereço a confirmar diretamente com o beneficiário")

        # FICHA DE COMPENSAÇÃO label
        y8 = y7 + 16
        self.set_xy(140, y8)
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(80, 80, 80)
        self.cell(55, 3, "FICHA DE COMPENSAÇÃO", align="R")

        # Código de barras (visual fake) + texto
        self._draw_codigo_barras_fake(y=y8 + 4)

    def _row_label_value(
        self, *, x: float, y: float,
        left_label: str, left_value: str,
        right_label: str, right_value: str,
        right_x: float, right_w: float,
    ) -> None:
        """Helper: linha com 1 par esquerda + 1 par direita (label cima/valor abaixo)."""
        self.set_xy(x, y)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(80, 80, 80)
        self.cell(right_x - x, 3, left_label)

        self.set_xy(right_x, y)
        self.cell(right_w, 3, right_label, align="R")

        self.set_xy(x, y + 3)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(0, 0, 0)
        self.cell(right_x - x, 5, left_value)

        self.set_xy(right_x, y + 3)
        self.cell(right_w, 5, right_value, align="R")

    def _cols_row(
        self, *, x: float, y: float, cols: list[tuple[str, str, float]],
        last_right: bool = False, last_bold: bool = False,
    ) -> None:
        """Helper: linha de N colunas (label cima + valor abaixo). Linhas verticais entre cols."""
        self.set_draw_color(200, 200, 200)
        cur_x = x
        # Cabeçalhos
        self.set_font("Helvetica", "", 7)
        self.set_text_color(80, 80, 80)
        for label, _, w in cols:
            self.set_xy(cur_x, y)
            self.cell(w, 3, label)
            cur_x += w

        # Valores
        cur_x = x
        self.set_text_color(0, 0, 0)
        for i, (_, val, w) in enumerate(cols):
            self.set_xy(cur_x, y + 3)
            is_last = i == len(cols) - 1
            if is_last and last_bold:
                self.set_font("Helvetica", "B", 10)
            else:
                self.set_font("Helvetica", "B" if last_bold and i == len(cols) - 1 else "", 8)
            align = "R" if (is_last and last_right) else "L"
            self.cell(w, 5, val, align=align)
            # Linha vertical separadora (exceto na última)
            if not is_last:
                self.line(cur_x + w, y, cur_x + w, y + 7)
            cur_x += w

    def _draw_codigo_barras_fake(self, *, y: float) -> None:
        """Desenha barras verticais simulando código de barras."""
        random.seed(hash(self.data.codigo_barras) % 100000)
        x = 15
        end_x = 130
        bar_h = 14
        self.set_fill_color(0, 0, 0)
        while x < end_x:
            w = random.uniform(0.3, 1.1)
            self.rect(x, y, w, bar_h, style="F")
            x += w + random.uniform(0.3, 0.7)

        # Texto do código + autenticação
        self.set_xy(15, y + bar_h + 1)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(60, 60, 60)
        self.cell(115, 3, self.data.codigo_barras, align="C")
        self.set_xy(15, y + bar_h + 4)
        self.set_font("Helvetica", "I", 6)
        self.set_text_color(150, 150, 150)
        self.cell(115, 3, "--- Autenticação Mecânica ---", align="C")

    # ── Marca d'água ────────────────────────────────────────────────────

    def _draw_marca_dagua(self) -> None:
        """SIMULAÇÃO diagonal, bem discreto via alpha real (~8%)."""
        self.set_text_color(160, 160, 160)
        self.set_font("Helvetica", "B", 90)
        with self.local_context(fill_opacity=0.08, stroke_opacity=0.08):
            with self.rotation(angle=-30, x=105, y=148):
                self.set_xy(15, 130)
                self.cell(180, 35, "SIMULAÇÃO", align="C")
        self.set_text_color(0, 0, 0)


# ── API pública ─────────────────────────────────────────────────────────


def gerar_pdf(data: BoletoFicticioInput) -> bytes:
    """
    Gera o PDF do boleto fictício e devolve bytes prontos para enviar
    via Telegram sendDocument.
    """
    pdf = _BoletoPDF(data)
    pdf.render()
    out = pdf.output()
    if isinstance(out, (bytes, bytearray)):
        return bytes(out)
    if isinstance(out, str):
        return out.encode("latin-1")
    return bytes(out)
