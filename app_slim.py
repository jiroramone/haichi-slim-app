from pathlib import Path

content = Path('output/app_slim.py').read_text(encoding='utf-8')
print(content[:200])
print('---')
print(len(content))
print('print(os.path.getsize' in content)
