"""Detect collapsed readable text at desktop and phone widths during crawling."""


def check_readable_layout(page):
    original = page.viewport_size
    results = []
    try:
        for width in (1440, 1024, 390):
            page.set_viewport_size({"width": width, "height": 1000})
            page.wait_for_timeout(50)
            collapsed = page.evaluate("""() => Array.from(document.querySelectorAll(
                'main h1, main h2, main h3, main p, main .form-text'
            )).filter(node => {
                const text = node.textContent.trim();
                const box = node.getBoundingClientRect();
                const style = getComputedStyle(node);
                return text.length > 12 && box.height > 0 && box.width >= 0 &&
                    style.visibility !== 'hidden' && box.width < parseFloat(style.fontSize) * 4;
            }).slice(0, 12).map(node => ({tag: node.tagName,
                width: Math.round(node.getBoundingClientRect().width),
                height: Math.round(node.getBoundingClientRect().height)}))""")
            results.append({"viewport_width": width, "collapsed_text": collapsed})
    finally:
        if original:
            page.set_viewport_size(original)
    return results
