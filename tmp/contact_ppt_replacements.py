from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


out_dir = Path("tmp/pptx_replacements")
paths = sorted(out_dir.glob("*.png"))
cell_w, cell_h = 360, 245
cols = 3
rows = (len(paths) + cols - 1) // cols
sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
draw = ImageDraw.Draw(sheet)
font = ImageFont.load_default()

for index, path in enumerate(paths):
    img = Image.open(path).convert("RGB")
    img.thumbnail((330, 190))
    col = index % cols
    row = index // cols
    x = col * cell_w
    y = row * cell_h
    draw.rectangle([x, y, x + cell_w - 1, y + cell_h - 1], outline=(205, 205, 205))
    draw.text((x + 10, y + 10), path.name, fill=(0, 0, 0), font=font)
    sheet.paste(img, (x + (cell_w - img.width) // 2, y + 42 + (185 - img.height) // 2))

sheet_path = out_dir / "contact_sheet.jpg"
sheet.save(sheet_path, quality=92)
print(sheet_path)
