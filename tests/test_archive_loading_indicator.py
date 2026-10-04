from ui.widgets.archive_loading_indicator import ArchiveLoadingIndicator


def test_shows_the_first_task_immediately():
    ind = ArchiveLoadingIndicator(["SPC & NWS", "Satellite", "Radar"])
    assert ind.text() == "Loading SPC & NWS…"
    assert ind.isVisible()


def test_advances_to_the_next_outstanding_task_in_order():
    ind = ArchiveLoadingIndicator(["SPC & NWS", "Satellite", "Radar"])
    ind.set_task_done("SPC & NWS")
    assert ind.text() == "Loading Satellite…"
    ind.set_task_done("Satellite")
    assert ind.text() == "Loading Radar…"


def test_a_task_finishing_out_of_order_does_not_change_the_displayed_task():
    """Tasks complete in whatever order their fetchers actually finish in
    (e.g. Radar can't start until Mesonets picks a station) -- the
    indicator always shows the *first* still-outstanding task in the
    original list order, not whichever one just finished."""
    ind = ArchiveLoadingIndicator(["SPC & NWS", "Satellite", "Radar"])
    ind.set_task_done("Radar")
    assert ind.text() == "Loading SPC & NWS…"


def test_hides_and_emits_all_done_once_every_task_completes():
    ind = ArchiveLoadingIndicator(["SPC & NWS", "Satellite"])
    fired = []
    ind.all_done.connect(lambda: fired.append(True))
    ind.set_task_done("SPC & NWS")
    assert fired == []
    assert ind.isVisible()
    ind.set_task_done("Satellite")
    assert fired == [True]
    assert not ind.isVisible()


def test_all_done_fires_exactly_once_even_with_redundant_calls():
    ind = ArchiveLoadingIndicator(["SPC & NWS"])
    fired = []
    ind.all_done.connect(lambda: fired.append(True))
    ind.set_task_done("SPC & NWS")
    ind.set_task_done("SPC & NWS")  # e.g. a stale retry firing after completion
    assert fired == [True]


def test_a_failed_task_counts_toward_completion_like_a_done_one():
    ind = ArchiveLoadingIndicator(["SPC & NWS", "Satellite"])
    ind.set_task_error("SPC & NWS")
    assert ind.text() == "Loading Satellite…"


def test_set_status_overrides_the_generic_text_for_the_current_task():
    ind = ArchiveLoadingIndicator(["Radar"])
    ind.set_status("Fetching first radar scan…")
    assert ind.text() == "Fetching first radar scan…"


def test_status_override_clears_once_that_task_completes():
    ind = ArchiveLoadingIndicator(["Radar", "Mobile Radar"])
    ind.set_status("Fetching first radar scan…")
    ind.set_task_done("Radar")
    assert ind.text() == "Loading Mobile Radar…"


def test_empty_task_list_starts_already_hidden():
    ind = ArchiveLoadingIndicator([])
    assert not ind.isVisible()


def test_later_background_work_shows_after_startup_without_reopening_startup():
    from ui.widgets.archive_loading_indicator import ArchiveLoadingIndicator
    ind = ArchiveLoadingIndicator(["Radar"])
    done = []
    ind.all_done.connect(lambda: done.append(True))
    ind.begin("lidar", "Loading Lidar…")
    assert ind.text() == "Loading Radar…"           # startup comes first
    ind.set_task_done("Radar")
    assert done == [True] and not ind.startup_pending()
    assert ind.text() == "Loading Lidar…" and not ind.isHidden()
    ind.begin("stop", "Loading Lidar truck · stop 2 of 2…")
    assert ind.text() == "Loading Lidar truck · stop 2 of 2…"     # the latest
    ind.end("stop")
    assert ind.text() == "Loading Lidar…"
    ind.end("lidar")
    assert ind.isHidden() and done == [True]         # all_done only once
    ind.end("lidar")                                 # ending twice is harmless
