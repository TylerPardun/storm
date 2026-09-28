from ui.controls.map_controls import MapControls


def test_annotations_are_shown_by_default_and_can_be_hidden(qtbot=None):
    controls = MapControls()
    seen = []
    controls.btn_annotations.toggled.connect(seen.append)
    assert controls.btn_annotations.isCheckable() and controls.btn_annotations.isChecked()
    controls.btn_annotations.click()
    assert seen == [False]
