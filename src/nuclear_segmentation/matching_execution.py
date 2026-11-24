"""Qt worker boundary for a captured, project-owned matching pair."""
from qtpy.QtCore import QObject, QThread, Signal, Slot

from .automatic_matching import classify_population, scan_overlaps


class MatchingWorker(QObject):
    progress = Signal(int)
    succeeded = Signal(object, object)
    failed = Signal(object, str)
    finished = Signal()

    def __init__(self, job):
        super().__init__()
        self.job = job

    @Slot()
    def run(self):
        job = self.job
        try:
            stats = job.cache
            if stats is None:
                stats = scan_overlaps(job.snapshot, self.progress.emit)
            result = classify_population(stats, job.settings, job.snapshot.measurement_revisions)
        except Exception as error:
            self.failed.emit(job, str(error))
        else:
            self.succeeded.emit(job, result)
        finally:
            self.finished.emit()


class MatchingExecution(QObject):
    changed = Signal()
    progress_changed = Signal(int)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self._thread = self._worker = None
        self.percent = 0

    @property
    def busy(self):
        return self._thread is not None

    def start(self):
        if self.busy:
            return
        try:
            job = self.state.matching_pair.begin()
        except ValueError:
            # Readiness may have changed since the last panel refresh.
            self.changed.emit()
            return
        worker = MatchingWorker(job)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._progress)
        worker.succeeded.connect(self._publish)
        worker.failed.connect(self._fail)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        self.percent = 100 if job.cache is not None else 0
        self.changed.emit()
        thread.start()

    @Slot(int)
    def _progress(self, value):
        self.percent = value
        self.progress_changed.emit(value)

    @Slot(object, object)
    def _publish(self, job, result):
        self.state.matching_pair.publish(job, result)
        self.changed.emit()

    @Slot(object, str)
    def _fail(self, job, message):
        self.state.matching_pair.fail(job, message)
        self.changed.emit()

    @Slot()
    def _finished(self):
        self._thread = self._worker = None
        self.changed.emit()
