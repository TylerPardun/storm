import pytest
from PyQt6.QtWidgets import QApplication

from ui.dialogs.task_progress_dialog import TaskProgressDialog


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


def test_finishing_closes_without_canceling():
    # close() goes through reject(), which is Cancel: a finished export used
    # to cancel itself (deleting the movie) and hang on "Canceling…"
    d = TaskProgressDialog("Export Video", "Rendering")
    canceled = []
    d.canceled.connect(lambda: canceled.append(1))
    d.show()
    d.finish()
    assert not d.isVisible() and not canceled
    d.close()
    d.reject()
    assert not canceled


def test_cancel_fires_once_then_finish_closes():
    d = TaskProgressDialog("Export Video", "Rendering")
    canceled = []
    d.canceled.connect(lambda: canceled.append(1))
    d.show()
    d.reject()                      # Esc / the window's close button
    d._cancel.click()
    assert canceled == [1] and d.isVisible()
    d.finish()
    assert not d.isVisible() and canceled == [1]
