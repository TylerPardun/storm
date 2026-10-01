from ui.tooltips import needs_wrapping, wrapped


def test_long_plain_descriptions_wrap_and_short_ones_do_not():
    long = "Show or hide annotations: fronts, boundaries and other drawings, annotation markers, and storm-motion cones"
    assert needs_wrapping(long)
    assert not needs_wrapping("Route directions")
    assert not needs_wrapping("<b>already rich text</b> " + long)


def test_wrapped_text_is_escaped_and_keeps_line_breaks():
    out = wrapped("a < b\nnext line")
    assert "a &lt; b<br>next line" in out and 'width="320"' in out


def test_tooltip_panel_has_only_the_theme_padding():
    """Qt adds its own label margin (11 px on macOS) inside the tooltip, which
    made every readout's panel far larger than its text."""
    import re
    from ui.theme import DARK_THEME
    rule = re.search(r"QToolTip\s*\{([^}]*)\}", DARK_THEME).group(1)
    assert "qproperty-margin: 0" in rule and "padding: 5px 9px" in rule
