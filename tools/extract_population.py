import os
import re

root = r'c:\Users\AFewMoon\Documents\Obsidian\提瓦特新世界'
files = []
for dirpath, dirnames, filenames in os.walk(root):
    relpath = os.path.relpath(dirpath, root)
    for f in filenames:
        if f.endswith('.md'):
            files.append(os.path.join(dirpath, f))

files.sort()
for filepath in files:
    relpath = os.path.relpath(filepath, root)
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if '人口' in line:
            print(f"{relpath} | 行{i+1}: {line.rstrip()}")