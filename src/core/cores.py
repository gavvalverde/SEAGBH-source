"""
Paleta Alura — centralizada para uso em QSS (Qt Style Sheets).
"""


class Cores:
    """Todas as cores do sistema, acessíveis como Cores.AZUL_ESCURO etc."""

    # ── Primárias ──
    AZUL_ESCURO    = "#031626"
    AZUL_ALURA     = "#0D3B66"
    AZUL_MEDIO     = "#6BD1FF"
    VERDE_ALURA    = "#00C86F"

    # ── Categorias / Acentos ──
    ROXO_ALURA     = "#7B71FF"
    ROSA_ALURA     = "#DC6EBE"
    LARANJA_ALURA  = "#FF8C2A"
    AMARELO_ALURA  = "#FFBA05"
    LIMA_ALURA     = "#9CD33B"
    VERMELHO_ALURA = "#F16165"

    # ── Neutros ──
    CINZA_ESCURO   = "#657176"
    CINZA_MEDIO    = "#E3E9ED"
    CINZA_CLARO    = "#EBEBEE"
    AZUL_GELO      = "#073343"
    BRANCO         = "#FFFFFF"

    # ── Tema Escuro — fundos ──
    BG_PAINEL      = "#071E38"
    BG_INPUT       = "#040E1A"
    BORDA_ESCURA   = "#1A3A5C"

    # ── Tags de status (escuro) ──
    VERDE_CLARO    = "#0D2E1A"
    LARANJA_CLARO  = "#2A1500"
    AMARELO_CLARO  = "#1F1800"
    VERMELHO_CLARO = "#2A0C0D"

    @classmethod
    def paleta_escura(cls) -> dict:
        """Retorna dict com valores do tema escuro."""
        return {
            "AZUL_ESCURO":    "#031626",
            "AZUL_ALURA":     "#0D3B66",
            "AZUL_MEDIO":     "#6BD1FF",
            "BG_PAINEL":      "#071E38",
            "BG_INPUT":       "#040E1A",
            "BORDA_ESCURA":   "#1A3A5C",
            "BRANCO":         "#FFFFFF",
            "CINZA_ESCURO":   "#657176",
            "CINZA_MEDIO":    "#E3E9ED",
            "AZUL_GELO":      "#073343",
            "VERDE_CLARO":    "#0D2E1A",
            "LARANJA_CLARO":  "#2A1500",
            "AMARELO_CLARO":  "#1F1800",
            "VERMELHO_CLARO": "#2A0C0D",
        }

    @classmethod
    def paleta_clara(cls) -> dict:
        """Retorna dict com valores do tema claro."""
        return {
            "AZUL_ESCURO":    "#F0F4F8",
            "AZUL_ALURA":     "#1E5A8A",
            "AZUL_MEDIO":     "#0369A1",
            "BG_PAINEL":      "#FFFFFF",
            "BG_INPUT":       "#E8EEF4",
            "BORDA_ESCURA":   "#94A3B8",
            "BRANCO":         "#0F172A",
            "CINZA_ESCURO":   "#475569",
            "CINZA_MEDIO":    "#FFFFFF",
            "AZUL_GELO":      "#DBEAFE",
            "VERDE_CLARO":    "#DCFCE7",
            "LARANJA_CLARO":  "#FFF7ED",
            "AMARELO_CLARO":  "#FEFCE8",
            "VERMELHO_CLARO": "#FEE2E2",
        }
