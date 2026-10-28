from dataclasses import dataclass, field
from threading import RLock
from uuid import uuid4
from ..domain.types import Asset, Revision
from ..domain.errors import ValidationError


@dataclass
class Project:
    project_id: str = field(default_factory=lambda: "proj-" + uuid4().hex)
    title: str = "Untitled session"
    assets: dict = field(default_factory=dict)
    revisions: dict = field(default_factory=dict)
    results: dict = field(default_factory=dict)
    findings: dict = field(default_factory=dict)
    edit_nodes: dict = field(default_factory=dict)
    edit_history: list = field(default_factory=list)
    history_position: int = -1
    laboratory: list = field(default_factory=list)
    calibrations: dict = field(default_factory=dict)
    environments: dict = field(default_factory=dict)
    generation: int = 0
    lock: RLock = field(default_factory=RLock)

    def register(self, obj):
        asset = Asset.from_dict(obj)
        if asset.frames <= 0 or not (1 <= asset.channels <= 128) or asset.sample_rate <= 0:
            raise ValidationError("Invalid decoded audio specification.")
        self.assets[asset.asset_id] = asset
        self.revisions[asset.original_revision_id] = Revision(
            asset.original_revision_id, asset.asset_id, asset.frames, asset.sample_rate, asset.channels
        )
        return asset

    def snapshot(self):
        with self.lock:
            return {
                "project_id": self.project_id,
                "title": self.title,
                "laboratory": self.laboratory,
                "assets": [asset.to_dict() for asset in self.assets.values()],
                "results": list(self.results.values()),
                "findings": list(self.findings.values()),
                "edit_nodes": self.edit_nodes,
                "edit_history": self.edit_history,
                "history_position": self.history_position,
                "calibrations": self.calibrations,
                "environments": self.environments,
            }

    def restore_science(self, snapshot):
        import json
        from ..domain.types import ResultRef, Region

        records = snapshot.get("laboratory", [])
        if not isinstance(records, list) or len(records) > 10000 or len(json.dumps(records)) > 16 * 1024**2:
            raise ValidationError("Invalid laboratory history.")
        from ..runtime.cache import cache_key

        for record in records:
            if not isinstance(record, dict):
                raise ValidationError("Invalid laboratory receipt.")
            data = dict(record)
            digest = data.pop("receipt_sha256", None)
            if digest != cache_key(data):
                raise ValidationError("Laboratory receipt digest mismatch.")
        self.laboratory = records
        results = snapshot.get("results", [])
        findings = snapshot.get("findings", [])
        if len(results) > 10000 or len(findings) > 50000:
            raise ValidationError("Too many saved scientific records.")
        for value in results:
            reference = ResultRef.from_dict(value)
            region = Region(**dict(reference.region))
            if region.revision_id not in self.revisions:
                raise ValidationError("Saved analysis refers to an unknown revision.")
            self.results[reference.result_id] = reference.to_dict()
        for finding in findings:
            required = {
                "finding_id",
                "revision_id",
                "start_frame",
                "end_frame",
                "channels",
                "label",
                "review_status",
            }
            if not required <= set(finding):
                raise ValidationError("Invalid saved finding.")
            region = Region(
                finding["revision_id"],
                finding["start_frame"],
                finding["end_frame"],
                tuple(finding["channels"]),
            )
            revision = self.revisions.get(region.revision_id)
            if (
                revision is None
                or region.end_frame > revision.frames
                or max(region.channels) >= revision.channels
                or finding["review_status"] not in {"unreviewed", "accepted", "rejected"}
                or len(finding["label"]) > 512
            ):
                raise ValidationError("Invalid saved finding bounds/status.")
            json.dumps(finding, allow_nan=False)
            self.findings[finding["finding_id"]] = dict(finding)

    def restore_edits(self, snapshot):
        import json
        from ..runtime.cache import cache_key

        nodes = snapshot.get("edit_nodes", {})
        if not isinstance(nodes, dict) or len(nodes) > 10000:
            raise ValidationError("Too many or invalid edit nodes.")
        visiting = set()
        done = set()

        def visit(rev):
            if rev in visiting:
                raise ValidationError("Cyclic render graph.")
            if rev in done:
                return
            node = nodes.get(rev)
            if node:
                if node.get("version") != 1 or node.get("revision_id") != rev or rev not in self.revisions:
                    raise ValidationError("Invalid render node identity/version.")
                r = self.revisions[rev]
                asset = self.assets[r.asset_id]
                expected = cache_key(
                    {
                        "recipe": node.get("recipe"),
                        "parents": node.get("parents"),
                        "sample_rate": r.sample_rate,
                        "libraries": node.get("libraries"),
                        "engine_contract": 1,
                        "output_sha256": asset.sha256,
                    }
                )
                if (
                    asset.render_identity != expected
                    or node.get("recipe_id") != expected
                    or node.get("output_sha256") != asset.sha256
                ):
                    raise ValidationError("Render graph/output identity mismatch.")
                if len(visiting) >= 128:
                    raise ValidationError("Render graph depth exceeds 128 nodes.")
                for m in node.get("mappings", []):
                    parent = self.revisions.get(m.get("parent_revision"))
                    if (
                        parent is None
                        or not 0 <= m.get("input_start", -1) <= parent.frames
                        or not 0 <= m.get("input_end", -1) <= parent.frames
                        or not 0 <= m.get("output_start", -1) < m.get("output_end", -1) <= r.frames + 1e-6
                    ):
                        raise ValidationError("Invalid saved render mapping bounds.")
                visiting.add(rev)
                for parent in node.get("parents", []):
                    if parent not in self.revisions:
                        raise ValidationError("Missing parent revision.")
                    visit(parent)
                visiting.remove(rev)
                r = self.revisions[rev]
                self.revisions[rev] = Revision(
                    r.revision_id,
                    r.asset_id,
                    r.frames,
                    r.sample_rate,
                    r.channels,
                    tuple(node["parents"]),
                    "render",
                )
            done.add(rev)

        for rev in nodes:
            visit(rev)
        history = snapshot.get("edit_history", [])
        position = snapshot.get("history_position", -1)
        if (
            not isinstance(history, list)
            or len(history) > 10000
            or any(r not in self.revisions for r in history)
            or type(position) is not int
            or not -1 <= position < len(history)
        ):
            raise ValidationError("Invalid edit history.")
        self.edit_nodes = json.loads(json.dumps(nodes, allow_nan=False))
        self.edit_history = list(history)
        self.history_position = position
        for key, profile in snapshot.get("calibrations", {}).items():
            saved = dict(profile)
            ident = saved.pop("calibration_id", None)
            if key != ident or ident != "cal-" + cache_key(saved):
                raise ValidationError("Invalid calibration profile digest.")
            self.calibrations[key] = profile

        for key, profile in snapshot.get("environments", {}).items():
            saved = dict(profile)
            ident = saved.pop("environment_id", None)
            if ident != key or ident != "env-" + cache_key(saved):
                raise ValidationError("Invalid environment profile digest.")
            self.environments[key] = profile
