from pathlib import Path
src = Path('output/app_slim.py').read_text(encoding='utf-8')
print(len(src))
print(src[:100])
print(src[-200:])
