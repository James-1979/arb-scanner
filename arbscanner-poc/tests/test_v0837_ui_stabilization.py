from pathlib import Path
import re
from collections import Counter

from arbscanner import __version__

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'frontend' / 'index.html').read_text()
STYLE = HTML.split('<style>', 1)[1].split('</style>', 1)[0]


def _css_braces_balanced(css: str) -> bool:
    depth = 0
    quote = None
    escaped = False
    in_comment = False
    i = 0
    while i < len(css):
        c = css[i]
        n = css[i + 1] if i + 1 < len(css) else ''
        if in_comment:
            if c == '*' and n == '/':
                in_comment = False
                i += 2
                continue
        elif quote:
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == quote:
                quote = None
        else:
            if c == '/' and n == '*':
                in_comment = True
                i += 2
                continue
            if c in "'\"":
                quote = c
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth < 0:
                    return False
        i += 1
    return depth == 0 and quote is None and not in_comment


def test_v0837_release_and_css_parser_integrity():
    assert __version__ == '0.9.36'
    assert '<title>ArbScanner PoC 0.9.36</title>' in HTML
    assert HTML.count('<style>') == 1
    assert _css_braces_balanced(STYLE)
    # Regression for the malformed-selector prefix incident.
    assert not re.search(r'(?m)^n(?:[.#:@]|/\*)', STYLE)
    assert 'v0.8.42 UI stabilization' in STYLE


def test_v0837_frontend_patch_layers_are_consolidated():
    names = re.findall(r'\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(', HTML)
    duplicates = {name: count for name, count in Counter(names).items() if count > 1}
    assert duplicates == {}
    assert HTML.count('async function loadMarketAnalysis(){') == 1
    assert '__loadMarketAnalysis0835' not in HTML
    assert '__loadMarketAnalysis0836' not in HTML
    assert 'renderMarketDropoff' not in HTML
    assert 'timelineReplayAccounts' not in HTML


def test_v0837_operator_ui_contracts_are_present():
    compact = ''.join(STYLE.split())
    assert '.nav-count-badge[hidden]{display:none!important}' in compact
    assert '.result-bestworst .best{color:var(--good)}' in STYLE
    assert '.result-bestworst .worst{color:var(--bad)}' in STYLE
    assert '.market-summary-card .helpq{position:absolute;top:8px;right:8px' in STYLE
    assert 'rows=rows.slice(0,10)' in HTML
    assert 'id="marketSportsPreDiscovery"' in HTML and 'id="marketSportsInplayDiscovery"' in HTML and 'id="marketRacingDiscovery"' in HTML
    assert 'Conversion / drop-off' not in HTML
    assert 'id="timelineReplayAccounts"' not in HTML
    assert 'id="timelineReplayPnlChart"' not in HTML
