"""Validate current bilingual documentation and local Markdown destinations."""
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ('zh-CN', 'en')


def documents(root):
    result = list((root / 'docs').rglob('*.md'))
    result.extend(root.glob('*.md'))
    for directory in ('vscode', 'examples'):
        base = root / directory
        if directory == 'vscode':
            result.extend(base.glob('README*.md'))
        else:
            result.extend(base.rglob('README*.md'))
    return sorted(set(result))


def heading_anchors(text):
    """GitHub-style anchors for the headings used by our Markdown pages."""
    text = re.sub(r'```.*?```', '', text, flags=re.S)
    counts = {}
    anchors = set()
    for match in re.finditer(r'^#{1,6}\s+(.+?)\s*#*$', text, re.M):
        label = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', match[1])
        label = re.sub(r'<[^>]+>', '', label)
        slug = re.sub(r'[^\w\- ]', '', label.lower()).strip().replace(' ', '-')
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(slug if count == 0 else f'{slug}-{count}')
    return anchors


def check(root=ROOT):
    root = Path(root)
    errors = []
    docs = root / 'docs'
    for entry in docs.iterdir():
        if entry.name not in {*LANGUAGES, 'README.md'}:
            errors.append(f'{entry.relative_to(root)}: unexpected documentation entry')
    sets = {lang: {p.relative_to(docs / lang) for p in (docs / lang).rglob('*.md')}
            for lang in LANGUAGES}
    for lang in LANGUAGES:
        if not sets[lang]:
            errors.append(f'docs/{lang}: missing documentation')
        other = LANGUAGES[1] if lang == LANGUAGES[0] else LANGUAGES[0]
        for missing in sorted(sets[other] - sets[lang]):
            errors.append(f'docs/{lang}/{missing}: missing translation')
        for path in (docs / lang).rglob('*'):
            if path.is_file() and path.suffix != '.md':
                errors.append(f'{path.relative_to(root)}: documentation must not contain run artifacts')
            if path.is_dir() and path.name in {'archive', 'evidence', 'releases'}:
                errors.append(f'{path.relative_to(root)}: historical documentation is not retained')
    for directory in [root, root / 'vscode', root / 'examples', *sorted((root / 'examples').glob('*'))]:
        if not directory.is_dir():
            continue
        translation = 'README.zh-CN.md' if directory == root else 'README.en.md'
        if (directory / 'README.md').exists() != (directory / translation).exists():
            errors.append(f'{directory.relative_to(root)}: README requires Chinese and English versions')
    for document in documents(root):
        raw = document.read_text()
        if len(re.findall(r'^\s*```', raw, re.M)) % 2:
            errors.append(f'{document.relative_to(root)}: unclosed code fence')
        text = re.sub(r'```.*?```', '', raw, flags=re.S)
        text = re.sub(r'`[^`]*`', '', text)
        relative = document.relative_to(root)
        titles = re.findall(r'^#\s+.+|<h1\b[^>]*>.*?</h1>', text, re.M | re.I)
        if len(titles) != 1:
            errors.append(f'{relative}: expected one page title')
        if len(relative.parts) >= 3 and relative.parts[:2] in [('docs', lang) for lang in LANGUAGES]:
            lang = relative.parts[1]
            other = LANGUAGES[1] if lang == LANGUAGES[0] else LANGUAGES[0]
            counterpart = docs / other / Path(*relative.parts[2:])
            targets = []
            for match in re.finditer(r'\[[^\]]*\]\(([^\s)]+)\)', text):
                url = urlsplit(match[1])
                if not url.scheme and not url.netloc:
                    targets.append((document.parent / unquote(url.path)).resolve())
            if counterpart.resolve() not in targets:
                errors.append(f'{relative}: missing language switch')
            if (docs / lang / 'README.md').resolve() not in targets:
                errors.append(f'{relative}: missing documentation home link')
        for match in re.finditer(r'(?<![\w.])!?\[[^\]]*\]\(([^\s)]+)(?:\s+"[^\"]*")?\)', text):
            target = match[1].strip('<>')
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            path = (document.parent / unquote(url.path)).resolve() if url.path else document.resolve()
            if not path.exists():
                errors.append(f'{relative}: missing {target}')
            elif url.fragment and path.suffix == '.md':
                if unquote(url.fragment) not in heading_anchors(path.read_text()):
                    errors.append(f'{relative}: missing heading {target}')
        for match in re.finditer(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', text, re.I):
            url = urlsplit(match[1])
            if not url.scheme and not url.netloc and not (document.parent / unquote(url.path)).exists():
                errors.append(f'{relative}: missing image {match[1]}')
    return errors


if __name__ == '__main__':
    errors = check()
    print('\n'.join(errors) if errors else 'Bilingual pages, navigation and local Markdown destinations are valid.')
    sys.exit(bool(errors))
