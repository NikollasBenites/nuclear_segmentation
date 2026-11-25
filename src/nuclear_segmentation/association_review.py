"""Manual association decisions owned by one project's matching pair context."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from weakref import ref

STATES = ("Not Reviewed", "Matched", "Unmatched", "Uncertain")


def dependencies(result):
    grid, nuclear, cyto = result.statistics.population_identity
    acquisition = asdict(grid)
    if grid.source_sha256:
        acquisition.pop("source_path")
    sources = []
    for owner, channel, ids in (nuclear, cyto):
        population = hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()
        sources.append(dict(session=owner.session_id, input=owner.input_revision,
                            geometry=owner.geometry_revision, channel=channel, population=population))
    return dict(grid=acquisition, sources=sources)


class AssociationReview:
    def __init__(self, pair):
        self.pair = pair
        self.events = []
        self.records = {}
        self.stack = []
        self._stats = self._dependencies = None

    def identity(self, result):
        if self._stats is None or self._stats() is not result.statistics:
            self._dependencies = dependencies(result)
            self._stats = ref(result.statistics)
        return self._dependencies

    def key(self, label):
        return (self.pair.session_id("nuclear"), self.pair.session_id("cytoplasmic"), label)

    def status(self, result, label):
        event = self.records.get(self.key(label))
        if event is None:
            return STATES[0]
        if (event["dependencies"] != self.identity(result) or label not in result.statistics.nuclear_voxels
                or event["status"] == "Matched" and event["cytoplasmic_id"] not in result.statistics.cytoplasmic_voxels):
            return "Requires Review"
        return event["status"]

    def decide(self, result, label, status, target=None, comment=""):
        if result is None or result is not self.pair.current_result:
            raise ValueError("Run current automatic matching before recording a review.")
        if label not in result.statistics.nuclear_voxels or status not in STATES:
            raise ValueError("Select a retained nucleus and a review state.")
        if status == "Matched" and target not in result.statistics.cytoplasmic_voxels:
            raise ValueError("Select a retained cytoplasmic object.")
        row = next(r for r in result.nuclei if r.nuclear_id == label)
        key = self.key(label)
        event = dict(action="decision", nuclear_session=key[0], cytoplasmic_session=key[1],
                     nuclear_id=label, cytoplasmic_id=target if status == "Matched" else None,
                     status=status, comment=comment, origin="manual", dependencies=self.identity(result),
                     timestamp=datetime.now(timezone.utc).isoformat(), automatic_class=row.auto_class,
                     automatic_reason=row.classification_reason,
                     automatic_target=row.accepted_cytoplasmic_id, settings=asdict(result.settings))
        self.append(event)

    def append(self, event):
        key = event["nuclear_session"], event["cytoplasmic_session"], event["nuclear_id"]
        if event["action"] == "decision":
            self.stack.append((len(self.events), key, self.records.get(key)))
            self.records[key] = event
        else:
            index = next(i for i in reversed(range(len(self.stack))) if self.stack[i][1][:2] == key[:2])
            original, undone_key, previous = self.stack[index]
            if original != event["undone"] or undone_key != key:
                raise ValueError("Invalid association undo history.")
            self.stack.pop(index)
            if previous is None:
                self.records.pop(key)
            else:
                self.records[key] = previous
        self.events.append(event)

    def undo(self):
        pair_key = self.key(None)[:2]
        for index, key, _ in reversed(self.stack):
            if key[:2] == pair_key:
                self.append(dict(action="undo", undone=index, nuclear_session=key[0], cytoplasmic_session=key[1],
                                 nuclear_id=key[2], timestamp=datetime.now(timezone.utc).isoformat()))
                return
        raise ValueError("No decision to undo for this pair.")


def reviewed_outcomes(result, review):
    for row in result.nuclei:
        status = review.status(result, row.nuclear_id)
        event = review.records.get(review.key(row.nuclear_id))
        target = event["cytoplasmic_id"] if status == "Matched" else None
        yield row, status, target
