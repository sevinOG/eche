# Assets

- `eche.ico` - optional icon for installer EXE. Replace with your own.
  To generate a simple icon, run:

```bash
python -m pip install Pillow
python -c "
from PIL import Image, ImageDraw
img = Image.new('RGBA', (256,256), (11,14,20,255))
draw = ImageDraw.Draw(img)
draw.rectangle([32,32,224,224], fill=(139,92,246,255))
draw.text((80,110), 'E', fill=(255,255,255,255), font_size=120)
img.save('assets/eche.ico', sizes=[(256,256),(128,128),(64,64),(32,32),(16,16)])
print('icon created')
"
```

Without icon, PyInstaller will use default.

Theme reference:
- Primary icon should be dark background with violet E, matching Eche tactical brand.
