from app.rag.ingest import _in_scope, normalize_url


def test_normalize_url_drops_query_and_fragment():
    assert normalize_url("https://site.getnet.com.br/duvidas/app-getnet/?modal_open=154#x") == \
        "https://site.getnet.com.br/duvidas/app-getnet/"


def test_scope_keeps_faq_and_skips_blog_and_files():
    root = "https://site.getnet.com.br/duvidas/"
    assert _in_scope("https://site.getnet.com.br/duvidas/pix/", root)
    assert not _in_scope("https://site.getnet.com.br/blog/post", root)
    assert not _in_scope("https://site.getnet.com.br/duvidas/carreira/", root)
    assert not _in_scope("https://site.getnet.com.br/duvidas/manual.pdf", root)
    assert not _in_scope("https://www.getnet.net/en/resources/blog/x", "https://www.getnet.net/en/")
