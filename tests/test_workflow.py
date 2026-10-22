from pathlib import Path

import yaml

from engine.artifact_manager import finalize
from engine.pipeline import LanguageResult
from engine.quality_checker import QualityReport
from engine.reporting import overall_status, step_summary
from engine.results import Status

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"


def load(name):
    data = yaml.safe_load((WF / name).read_text(encoding="utf-8"))
    # PyYAML parses the `on:` key as boolean True
    data["on"] = data.pop(True, data.get("on"))
    return data


def test_primary_workflow_is_one_click():
    wf = load("0-make-video.yml")
    inputs = wf["on"]["workflow_dispatch"]["inputs"]
    assert len(inputs) <= 25
    assert all(not spec.get("required", False) for spec in inputs.values())  # Run workflow with no input
    assert inputs["target_languages"]["default"] == "auto"
    assert inputs["output_format"]["default"] == "shorts_9_16"
    assert inputs["max_duration"]["default"] == "180"
    for key in ("english_script", "english_audio", "urdu_script", "urdu_audio", "roman_urdu_script", "roman_urdu_audio", "pashto_script", "pashto_audio"):
        assert key in inputs


def test_workflow_uploads_artifacts_with_retention():
    wf = load("0-make-video.yml")
    steps = [s for job in wf["jobs"].values() for s in job["steps"]]
    upload = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert upload and "retention-days" in upload[0]["with"]
    assert wf.get("permissions", {}).get("contents") == "read"


def test_workflow_inputs_passed_via_env_not_shell_interpolation():
    text = (WF / "0-make-video.yml").read_text(encoding="utf-8")
    run_blocks = [s.get("run", "") for job in load("0-make-video.yml")["jobs"].values() for s in job["steps"]]
    for block in run_blocks:
        assert "${{ inputs." not in block and "${{ github.event.inputs." not in block
    assert "INPUT_ENGLISH_SCRIPT" in text


def test_legacy_workflow_exists():
    wf = load("3-make-english-urdu.yml")
    assert "workflow_dispatch" in wf["on"]


def test_status_policy():
    ok = LanguageResult("english", "en", Status.SUCCESS)
    bad = LanguageResult("pashto", "ps", Status.PERMANENT_FAILURE, "no voice")
    skip = LanguageResult("urdu", "ur", Status.SKIPPED)
    assert overall_status([ok, skip], None) == ("success", 0)
    assert overall_status([ok, bad], None) == ("partial_success", 0)
    assert overall_status([bad, skip], None) == ("failure", 1)
    assert overall_status([skip], None) == ("nothing_to_do", 0)
    md = step_summary([ok, bad, skip], "partial_success", "whiteboard-video-output")
    assert "PERMANENT_FAILURE" in md and "SKIPPED" in md and "whiteboard-video-output" in md


def test_failed_language_outputs_not_published(tmp_path):
    (tmp_path / "pashto").mkdir()
    (tmp_path / "pashto" / "video.mp4").write_bytes(b"partial")
    finalize(tmp_path, [LanguageResult("pashto", "ps", Status.PERMANENT_FAILURE, "x")])
    assert not (tmp_path / "pashto").exists()
    assert (tmp_path / "README.txt").exists()


def test_quality_report_rejects_failures():
    r = QualityReport()
    r.add("a", True, True)
    r.add("b", False, False)
    assert r.passed
    r.add("c", False, True)
    assert not r.passed and r.to_dict()["critical_failures"] == ["c"]


def test_research_workflow_is_one_click():
    wf = load("1-research-viral-topics.yml")
    inputs = wf["on"]["workflow_dispatch"]["inputs"]
    assert all(not spec.get("required", False) for spec in inputs.values())
    steps = wf["jobs"]["research"]["steps"]
    for s in steps:
        assert "${{ inputs." not in s.get("run", "")
    upload = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert upload and upload[0]["with"]["name"] == "viral-topic-research"
    assert wf["permissions"]["contents"] == "read"


def test_two_click_workflows_are_safe():
    import re

    for name, required in (("0-research-prepare.yml", {"topic"}), ("1-render-video.yml", {"research_run_id"})):
        wf = load(name)
        inputs = wf["on"]["workflow_dispatch"]["inputs"]
        assert len(inputs) <= 10 and {k for k, v in inputs.items() if v.get("required")} == required
        assert wf["permissions"]["contents"] == "read"  # never commits or pushes
        text = (WF / name).read_text(encoding="utf-8")
        for step in (s for job in wf["jobs"].values() for s in job["steps"]):
            run = step.get("run", "")
            assert "${{ inputs." not in run and "${{ github.event.inputs" not in run, f"{name}: input interpolated into shell"
            assert "git push" not in run and "git commit" not in run
        uploads = [s for job in wf["jobs"].values() for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]
        assert uploads and all("retention-days" in s["with"] for s in uploads)
        assert not re.search(r"secrets\.[A-Z_]+ }}\s*$", text.split("env:")[0])  # secrets only via env blocks


def test_research_workflow_args_match_cli():
    import re

    wf = (WF / "0-research-prepare.yml").read_text(encoding="utf-8")
    used = set(re.findall(r"(--[a-z-]+)", wf.split("- name: Research")[1].split("- name: Upload")[0]))
    known = set(re.findall(r'r\.add_argument\("(--[a-z-]+)"', (ROOT / "engine" / "cli.py").read_text(encoding="utf-8")))
    assert used and used <= known, used - known
