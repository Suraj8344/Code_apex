"""Shared deterministic (name -> price) DOM extraction, used by the Explorer
(catalog ledger) and the Tester (Signal 6 price-mismatch facts)."""
PRICE_JS = """() => {
  const norm = s => (s||'').replace(/^[^\\w\\d\\u00C0-\\u024F]+\\s*/, '').trim().slice(0, 60);
  const pairs = [];
  const leaves = Array.from(document.querySelectorAll('*')).filter(e => {
    const t = (e.textContent || '').trim();
    return e.children.length === 0 && /^-?\\$\\d{1,6}\\.\\d{2}$/.test(t);
  });
  for (const e of leaves) {
    const t = (e.textContent || '').trim().replace('$', '');
    const cents = Math.round(parseFloat(t) * 100);
    const box = e.closest('article, li, tr, [class*=card], [class*=product], [class*=item]')
             || e.parentElement;
    if (!box) continue;
    const nameNode = box.querySelector('h1, h2, h3, a, strong, b, td');
    const name = norm(nameNode ? nameNode.textContent : '');
    if (name.length > 2 && !/^\\d/.test(name) && !name.toLowerCase().includes('total')
        && !name.toLowerCase().includes('subtotal')) {
      pairs.push([name, cents]);
    }
  }
  return pairs;
}"""


def extract_pairs(page) -> list[tuple[str, int]]:
    try:
        raw = page.evaluate(PRICE_JS)
        return [(n, int(c)) for n, c in raw]
    except Exception:
        return []
