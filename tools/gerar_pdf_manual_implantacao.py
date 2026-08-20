from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from pathlib import Path
import argparse

BASE_DIR = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gera PDF do manual de implantacao")
    parser.add_argument(
        "--input",
        default=str(BASE_DIR / "docs" / "manual_implantacao_postgresql.txt"),
        help="Arquivo de texto de entrada",
    )
    parser.add_argument(
        "--output",
        default=str(BASE_DIR / "docs" / "manual_implantacao_postgresql.pdf"),
        help="Arquivo PDF de saida",
    )
    return parser.parse_args()


args = parse_args()
input_path = Path(args.input)
output_path = Path(args.output)

# Use a Unicode font for robust rendering.
font_name = "DejaVuSans"
font_path = Path("C:/Windows/Fonts/DejaVuSans.ttf")
if not font_path.exists():
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font_name = "Arial"

pdfmetrics.registerFont(TTFont(font_name, str(font_path)))

c = canvas.Canvas(str(output_path), pagesize=A4)
width, height = A4
left_margin = 45
right_margin = 45
top_margin = 45
bottom_margin = 45
line_height = 14

max_chars = 105

def wrap_line(text, limit=max_chars):
    words = text.split(" ")
    if not words:
        return [""]
    lines = []
    cur = words[0]
    for w in words[1:]:
        test = f"{cur} {w}"
        if len(test) <= limit:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines

y = height - top_margin
c.setFont(font_name, 11)

for raw in input_path.read_text(encoding="utf-8").splitlines():
    segments = [""] if raw == "" else wrap_line(raw)
    for seg in segments:
        if y <= bottom_margin:
            c.showPage()
            c.setFont(font_name, 11)
            y = height - top_margin
        c.drawString(left_margin, y, seg)
        y -= line_height

c.save()
print(output_path)
