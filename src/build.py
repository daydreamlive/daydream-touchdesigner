#!/usr/bin/env python3
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RELAY_DIR = os.path.join(SCRIPT_DIR, 'relay')
DIST_HTML = os.path.join(RELAY_DIR, 'dist', 'index.html')
EXT_FILE = os.path.join(SCRIPT_DIR, 'DaydreamExt.py')

BEGIN_MARKER = '# RELAY_HTML_BEGIN'
END_MARKER = '# RELAY_HTML_END'


def run_vite_build():
    print('Building relay...')
    result = subprocess.run(
        ['npm', 'run', 'build'],
        cwd=RELAY_DIR,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print('Build failed:')
        print(result.stderr)
        sys.exit(1)
    print('Build complete.')


def read_dist_html():
    with open(DIST_HTML, 'r', encoding='utf-8') as f:
        return f.read()


def inject_html(html_content):
    with open(EXT_FILE, 'r', encoding='utf-8') as f:
        ext_content = f.read()

    begin_idx = ext_content.find(BEGIN_MARKER)
    end_idx = ext_content.find(END_MARKER)

    if begin_idx == -1 or end_idx == -1:
        print(f'Error: Markers not found in {EXT_FILE}')
        print(f'  Expected: {BEGIN_MARKER} and {END_MARKER}')
        sys.exit(1)

    escaped = html_content.replace("'''", r"\'\'\'")
    new_section = f"{BEGIN_MARKER}\nRELAY_HTML_TEMPLATE = '''{escaped}'''\n{END_MARKER}"

    new_content = ext_content[:begin_idx] + new_section + ext_content[end_idx + len(END_MARKER):]

    with open(EXT_FILE, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print(f'Injected {len(html_content)} bytes into DaydreamExt.py')


def main():
    run_vite_build()
    html = read_dist_html()
    inject_html(html)
    print('Done.')


if __name__ == '__main__':
    main()

