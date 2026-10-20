"""The committed split preserves the dormant paths' audited family baseline."""

import json
from pathlib import Path

from git_loopy.release_version import RELEASE_VERSION_PATHS


ROOT = Path(__file__).parents[3]


def test_committed_paths_declare_identity_and_carried_debt() -> None:
    paths = {
        file.stem: json.loads(file.read_text(encoding="utf-8"))
        for file in (ROOT / "git-loopy/paths").glob("*.json")
    }
    assert set(paths) == {"python", "shell", "powershell"}
    register = json.loads(
        (ROOT / "git-loopy/conformance/fixture-claims.json").read_text(encoding="utf-8")
    )
    for key, manifest in paths.items():
        assert manifest["schema_version"] == 1
        assert manifest["key"] == key
        assert manifest["event_schema_support"] == [1]
        assert manifest["owned_files"]
        assert manifest["gate_rows"]
        assert manifest["contract_support"]
        assert isinstance(manifest["artifacts"], list)

    python = paths["python"]
    assert python["state"] == "active"
    assert python["tag_namespace"] == "vX.Y.Z"
    assert python["members"] == ["python", "tui"]
    assert python["artifacts"] == ["git-loopy", "git-loopy-tui"]
    assert python["release_authority"] == {
        "file": "VERSION",
        "copies": [
            str(path) for path in RELEASE_VERSION_PATHS if str(path) != "VERSION"
        ],
    }
    assert python["gate_rows"] == ["Python suite", "Rust Dashboard core"]
    for key, attempt, ending, gates in [
        ("shell", 553, 834, ["Shell syntax", "Shell Orchestrator suites"]),
        ("powershell", 554, 835, ["PowerShell Orchestrator suites"]),
    ]:
        manifest = paths[key]
        assert manifest["state"] == "dormant"
        assert manifest["tag_namespace"] == f"{key}/vX.Y.Z"
        assert manifest["members"] == [key]
        assert manifest["gate_rows"] == gates
        assert manifest["contract_support"] == "2.26"
        assert manifest["release_authority"] == {
            "file": f"git-loopy/paths/{key}.json",
            "field": "baseline.release_version",
        }
        baseline = manifest["baseline"]
        assert baseline["release_version"] == "0.11.2"
        assert baseline["source_tag"] == "v0.11.2"
        assert baseline["source_commit"] == "a0f912b57c222f2525238d37ae268b0adb4c764f"
        assert baseline["owed_waivers"] == [
            {"fixture": "attempt-lifecycle.json", "issue": attempt},
            {"fixture": "status-endings.json", "issue": ending},
        ]
        carried = {row["fixture"]: row["issue"] for row in baseline["owed_waivers"]}
        sandbox = register["fixtures"]["sandbox.json"][key]
        assert sandbox["kind"] == "waived_owed"
        assert sandbox["issue"] == {"shell": 866, "powershell": 868}[key]
        assert carried == {
            fixture: rows[key]["issue"]
            for fixture, rows in register["fixtures"].items()
            if rows[key]["kind"] == "waived_owed" and fixture != "sandbox.json"
        }
