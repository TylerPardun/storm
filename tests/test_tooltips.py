from ui.tooltips import needs_wrapping, wrapped


def test_long_plain_descriptions_wrap_and_short_ones_do_not():
    long = "Show or hide annotations: fronts, boundaries and other drawings, annotation markers, and storm-motion cones"
    assert needs_wrapping(long)
    assert not needs_wrapping("Route directions")
    assert not needs_wrapping("<b>already rich text</b> " + long)


def test_wrapped_text_is_escaped_and_keeps_line_breaks():
    out = wrapped("a < b\nnext line")
    assert "a &lt; b<br>next line" in out and 'width="320"' in out
