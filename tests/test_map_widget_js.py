from ui.map.widget import _no_result


def test_scripts_sent_to_the_map_return_nothing():
    """A script's last value is sent back to Python; a MapLibre call returns
    the whole map (1.2 GB serialized), so every script must end in void 0."""
    wrapped = _no_result("map.setLayoutProperty('radar-overlay', 'visibility', 'none')")
    assert wrapped.endswith(";void 0;")
    assert _no_result("x = 1; // trailing comment").endswith("\n;void 0;")   # a comment can't swallow it
