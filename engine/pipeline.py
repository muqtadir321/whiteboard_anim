"""Per-language pipeline orchestration with isolation, resume and reporting."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import LANGUAGE_CODES, __version__
from .audio_processor import AudioError, build_final_track, detect_silences, normalize, validate_audio
from .audio_sync import build_timeline, order_by_mention
from .audio_timing import Timing, align_words, estimate_timing
from .caption_renderer import write_srt, write_vtt
from .config import QUALITY_PROFILES
from .illustration_composer import CAPTION_REGION, Composer, to_svg
from .input_discovery import LanguageInputs
from .language_detector import detect_language
from .quality_checker import QualityReport, check_file, check_frames, check_semantics, check_visuals
from .results import StageError, StageResult, Status
from .scene_planner import Segment, plan_scenes
from .scene_schema import validate_plan
from .script_analyzer import ScriptError, analyze_script, normalize_script, split_long_sentence
from .svg_draw_order import load_program
from .transcription_manager import Transcript, get_transcriber
from .video_assembler import assemble
from .voice_manager import synthesize_sentences
from .whiteboard_renderer import RenderScene, WhiteboardRenderer


@dataclass
class LanguageResult:
    key: str
    code: str
    status: Status
    message: str = ""
    stages: list[StageResult] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    quality: dict | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"language": self.key, "code": self.code, "status": self.status.value, "message": self.message,
                "outputs": self.outputs, "stages": [s.to_dict() for s in self.stages], "quality_passed": (self.quality or {}).get("passed")}


def file_hash(path: Path | None) -> str:
    if not path:
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class LanguageJob:
    def __init__(self, inputs: LanguageInputs, cfg: dict, out_root: Path, tmp_root: Path, log=print, transcriber=None, tts_chain=None,
                 script_text_override: str | None = None, reference_voice: Path | None = None, cloner=None):
        self.inp = inputs
        self.cfg = copy.deepcopy(cfg)
        self.key = inputs.key
        self.lang = inputs.code
        self.out = out_root / self.key
        self.tmp = tmp_root / self.key
        self.log = lambda m: log(f"[{self.key}] {m}")
        self.result = LanguageResult(self.key, self.lang, Status.SKIPPED)
        self._transcriber = transcriber
        self._tts_chain = tts_chain
        self.script_text_override = script_text_override
        self.reference_voice = reference_voice
        self.cloner = cloner

    # -- stage helper ------------------------------------------------------
    def stage(self, name: str, fn):
        st = StageResult(name, Status.SUCCESS)
        self.log(f"stage {name} ...")
        try:
            data = fn(st)
            if isinstance(data, dict):
                st.data.update(data)
        except StageError as exc:
            st.status, st.message = exc.status, str(exc)
            self.result.stages.append(st.done())
            raise
        except Exception as exc:  # unexpected bug: keep other languages alive
            st.status, st.message = Status.PERMANENT_FAILURE, f"{type(exc).__name__}: {exc}"
            st.data["traceback"] = traceback.format_exc()[-2000:]
            self.result.stages.append(st.done())
            raise StageError(st.message) from exc
        self.result.stages.append(st.done())
        self.log(f"stage {name}: {st.status.value} {st.message}")
        return st

    def run(self) -> LanguageResult:
        if not self.inp.has_content and not self.script_text_override:
            msg = "; ".join(self.inp.errors) if self.inp.errors else "no script or voice-over supplied"
            self.result.status = Status.PERMANENT_FAILURE if self.inp.errors else Status.SKIPPED
            self.result.message = msg
            return self.result
        self.tmp.mkdir(parents=True, exist_ok=True)
        try:
            self._run()
        except StageError as exc:
            self.result.status = exc.status if exc.status != Status.SUCCESS else Status.PERMANENT_FAILURE
            if self.result.status == Status.RETRYABLE_FAILURE:
                self.result.message = f"temporary failure (retries exhausted): {exc}"
            else:
                self.result.message = str(exc)
            self.log(f"FAILED: {exc}")
        return self.result

    # -- main ----------------------------------------------------------------
    def _run(self):
        cfg = self.cfg
        warnings: list[str] = list(self.inp.errors)
        script_text = None
        sentences: list[str] = []
        transcript: Transcript | None = None
        meta = self.result.metadata
        meta.update({"language": self.key, "code": self.lang, "engine_version": __version__, "inputs": self.inp.to_dict()})

        # 1. Script
        def do_script(st):
            nonlocal script_text, sentences
            raw = self.script_text_override if self.script_text_override is not None else (self.inp.script_path.read_bytes() if self.inp.script_path else None)
            if raw is None:
                st.status, st.message = Status.SKIPPED, "no script supplied"
                return
            try:
                script_text = normalize_script(raw)
            except ScriptError as exc:
                script_text = None
                if self.inp.audio_path and cfg["voice"]["transcribe_missing_scripts"]:
                    st.status, st.message = Status.PARTIAL_SUCCESS, f"script rejected ({exc}); will transcribe the audio"
                    st.warnings.append(str(exc))
                    return
                raise StageError(f"Invalid script: {exc}")
            det = detect_language(script_text, self.lang)
            st.data["detected_language"] = det.to_dict()
            if det.code != self.lang and det.confidence >= 0.8:
                st.warnings.append(f"script looks like '{det.code}' (confidence {det.confidence:.2f}) but was supplied as '{self.lang}'; using '{self.lang}'")
            sentences = [s.text for s in analyze_script(script_text, self.lang)]
            st.data["sentences"] = len(sentences)
            st.data["characters"] = len(script_text)

        self.stage("script", do_script)

        # 2. Audio validation
        audio_info = None

        def do_audio(st):
            nonlocal audio_info
            if not self.inp.audio_path:
                st.status, st.message = Status.SKIPPED, "no voice-over supplied"
                return
            try:
                audio_info = validate_audio(self.inp.audio_path, cfg["quality"]["minimum_audio_peak_db"])
                st.data["supplied_audio"] = audio_info.to_dict()
            except AudioError as exc:
                audio_info = None
                if script_text and cfg["voice"]["generate_missing"]:
                    st.status, st.message = Status.PARTIAL_SUCCESS, f"supplied audio invalid ({exc}); narration will be generated"
                    warnings.append(f"invalid audio: {exc}")
                    return
                raise StageError(f"Invalid voice-over: {exc}")

        self.stage("audio_validation", do_audio)

        if not script_text and not audio_info:
            raise StageError("Neither a usable script nor a usable voice-over is available", Status.PERMANENT_FAILURE)

        narration_wav = self.tmp / "narration.wav"
        timing: Timing | None = None
        voice_desc: dict = {}

        # 3. Transcription (audio without script) or alignment (both present)
        def get_tr():
            if self._transcriber is not None:
                return self._transcriber
            if cfg["transcription"]["engine"] == "none":
                return None
            return get_transcriber(cfg)

        def do_voice(st):
            nonlocal timing, voice_desc, transcript, sentences, script_text
            sr = cfg["video"]["audio_sample_rate"]
            if audio_info and cfg["voice"]["prefer_supplied_audio"]:
                info = normalize(self.inp.audio_path, narration_wav, sr, trim_silence=True)
                voice_desc = {"provider": "supplied", "source": str(self.inp.audio_path), "license": "user supplied", "commercial_use": "user responsibility"}
                st.data["normalized_audio"] = info.to_dict()
                tr = get_tr() if (not script_text or cfg["voice"]["transcribe_missing_scripts"]) else None
                if not script_text:
                    if not cfg["voice"]["transcribe_missing_scripts"]:
                        raise StageError("Voice-over has no script and transcription is disabled")
                    if tr is None:
                        raise StageError("Voice-over has no script and no speech-recognition engine is installed "
                                         "(install faster-whisper, see README)", Status.PERMANENT_FAILURE)
                    transcript = tr.transcribe(str(narration_wav), self.lang)
                    st.data["transcript_confidence"] = round(transcript.confidence, 3)
                    if transcript.confidence < cfg["transcription"]["minimum_confidence"]:
                        st.warnings.append(f"low transcription confidence ({transcript.confidence:.2f}); captions may contain errors")
                    script_text = transcript.text
                    sentences = [s.text for s in analyze_script(script_text, self.lang)]
                    timing, _ = align_words(sentences, transcript.words, info.duration)
                elif tr is not None:
                    try:
                        transcript = tr.transcribe(str(narration_wav), self.lang)
                        timing, sim = align_words(sentences, transcript.words, info.duration)
                        st.data["script_audio_similarity"] = round(sim, 3)
                        if sim < 0.45:
                            # Recognition too unreliable to trust word times: use pause-based timing instead.
                            st.warnings.append(f"speech recognition matched only {sim:.0%} of the script; using pause-based timing")
                            timing = None
                        elif sim < 0.6:
                            st.warnings.append(f"script and voice-over differ materially (word similarity {sim:.2f}); "
                                               "audio timing is authoritative, captions use the supplied script")
                    except StageError as exc:
                        st.warnings.append(f"alignment transcription failed ({exc}); using silence-based timing estimate")
                        timing = None
                if timing is None:
                    timing = estimate_timing(sentences, info.duration, detect_silences(narration_wav))
                return
            if not script_text:
                raise StageError("No script available to generate narration from")
            if not cfg["voice"]["generate_missing"]:
                raise StageError("No voice-over supplied and narration generation is disabled", Status.SKIPPED)
            key = hashlib.sha256(json.dumps([sentences, self.lang, cfg["voice"], file_hash(self.reference_voice)], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
            cache = self.tmp / f"voice_{key}.json"
            if cfg["automation"]["resume"] and cache.exists() and narration_wav.exists():
                data = json.loads(cache.read_text())
                from .audio_timing import TimedSentence

                timing = Timing([TimedSentence(**s) for s in data["timing"]], "exact", data["confidence"], {"resumed": True})
                voice_desc = data["provider"]
                st.message = "reused cached narration"
                return
            vr = synthesize_sentences(sentences, self.lang, cfg, self.tmp / "tts", self.log, self._tts_chain, self.reference_voice, self.cloner)
            shutil.copy(vr.audio_path, narration_wav)
            timing = vr.timing
            voice_desc = vr.provider
            voice_desc["attempts"] = vr.attempts
            if vr.derived_text:
                (self.tmp / "tts_text.txt").write_text("\n".join(vr.derived_text), encoding="utf-8")
                voice_desc["derived_text_file"] = "tts_text.txt"
            if vr.clone:
                voice_desc["clone"] = vr.clone
                if vr.clone.get("applied"):
                    st.message = "narration converted to the reference voice"
                else:
                    st.warnings.append(f"reference voice not applied: {vr.clone.get('reason')}")
            if vr.wrong_language:
                st.warnings.append("narration uses a different-language voice (allow_wrong_language_fallback)")
            cache.write_text(json.dumps({"timing": [s.__dict__ for s in timing.sentences], "confidence": timing.confidence, "provider": voice_desc}, ensure_ascii=False))

        self.stage("voice_over", do_voice)
        meta["voice"] = voice_desc
        meta["timing"] = timing.to_dict()

        # 4. Duration policy (checked before rendering)
        narration_duration = timing.sentences[-1].end
        intro, outro = cfg["video"]["intro_seconds"], cfg["video"]["outro_seconds"]
        total = narration_duration + intro + outro

        def do_duration(st):
            limit = cfg["video"]["maximum_duration_seconds"]
            st.data.update({"narration_seconds": round(narration_duration, 3), "total_seconds": round(total, 3), "limit_seconds": limit})
            if total > limit:
                if not cfg["video"]["allow_longer_than_maximum"]:
                    raise StageError(f"Narration is {narration_duration:.1f}s (video {total:.1f}s), above the {limit}s limit. The audio was not cut. "
                                     "Shorten the script or set video.allow_longer_than_maximum: true.", Status.PERMANENT_FAILURE)
                st.status, st.message = Status.PARTIAL_SUCCESS, "longer than maximum; rendering full length (allow_longer_than_maximum)"
            free_mb = shutil.disk_usage(self.tmp).free / 1e6
            st.data["free_disk_mb"] = round(free_mb)
            if free_mb < cfg["automation"]["minimum_free_disk_mb"]:
                raise StageError(f"Only {free_mb:.0f} MB free disk space; need {cfg['automation']['minimum_free_disk_mb']} MB", Status.PERMANENT_FAILURE)

        self.stage("duration_check", do_duration)

        # 5. Scene planning
        plan = None

        def do_plan(st):
            nonlocal plan
            segments = []
            max_s = cfg["planner"]["max_scene_seconds"]
            for i, ts in enumerate(timing.sentences):
                d = ts.end - ts.start
                if d > max_s and not ts.words:
                    pieces = split_long_sentence(ts.text, max(40, int(len(ts.text) * max_s / d)))
                    tot = sum(len(p) for p in pieces) or 1
                    t = ts.start
                    for p in pieces:
                        dd = d * len(p) / tot
                        segments.append(Segment(p, t, t + dd, i))
                        t += dd
                else:
                    segments.append(Segment(ts.text, ts.start, ts.end, i, ts.words))
            plan = plan_scenes(segments, self.lang, cfg, self.key)
            plan["timing"] = {"source": timing.source, "confidence": timing.confidence}
            validate_plan(plan)
            st.data["scenes"] = len(plan["scenes"])
            st.data["scene_types"] = [s["scene_type"] for s in plan["scenes"]]
            st.data["fallback_scenes"] = sum(1 for s in plan["scenes"] if s["fallback"])

        self.stage("scene_planning", do_plan)

        # 6. Illustration
        composed = []
        programs = []
        svg_dir = self.out / "scenes"

        def do_illustrate(st):
            svg_dir.mkdir(parents=True, exist_ok=True)
            comp = Composer(cfg, self.lang)
            for scene in plan["scenes"]:
                cs = comp.compose(scene)
                st.warnings.extend(cs.warnings)
                svg = to_svg(cs, comp.style)
                (svg_dir / f"{scene['scene_id']}.svg").write_text(svg, encoding="utf-8")
                composed.append(cs)
                programs.append(load_program(svg))
            st.data["svg_files"] = len(composed)
            st.data["strokes"] = sum(len(op_list.ops) for prog in programs for op_list in prog)

        self.stage("illustration", do_illustrate)

        # 7. Audio track + synchronisation
        final_audio = self.tmp / "final_audio.wav"
        timelines = []

        def do_sync(st):
            music = cfg["video"]["background_music_path"] if cfg["video"]["background_music_enabled"] else None
            if music and not Path(music).exists():
                st.warnings.append(f"background music not found: {music}; continuing without music")
                music = None
            info = build_final_track(narration_wav, final_audio, intro, outro, cfg["video"]["audio_sample_rate"], music, cfg["video"]["background_music_volume"])
            st.data["final_audio_seconds"] = round(info.duration, 3)
            pen = (900.0, 1500.0)
            n = len(plan["scenes"])
            for i, scene in enumerate(plan["scenes"]):
                programs[i] = order_by_mention(scene, programs[i])
                items = programs[i]
                shifted = dict(scene)
                shifted["start_seconds"] = 0.0 if i == 0 else scene["start_seconds"] + intro
                shifted["end_seconds"] = info.duration if i == n - 1 else scene["end_seconds"] + intro
                if shifted.get("words"):
                    shifted["words"] = [{**w, "start": w["start"] + intro, "end": w["end"] + intro} for w in shifted["words"]]
                tl = build_timeline(shifted, items, cfg, pen)
                if items and items[-1].ops:
                    pen = items[-1].ops[-1].end_point
                timelines.append(tl)
                scene["timeline"] = tl.summary()
            st.data["timing_source"] = timing.source
            st.data["compressed_scenes"] = [t.scene_id for t in timelines if t.compressed]
            if st.data["compressed_scenes"]:
                st.warnings.append("some scenes had to be drawn very fast to fit their narration")
            return {"total_seconds": info.duration}

        sync = self.stage("synchronisation", do_sync)
        total = sync.data["total_seconds"]

        # 8. Render & encode (one bounded fallback to the fast profile on failure)
        video_path = self.out / "video.mp4"
        render_stats = None
        renderer = None

        def do_render(st):
            nonlocal render_stats, renderer
            attempts = [cfg["video"]["quality"]] + (["fast"] if cfg["video"]["quality"] != "fast" else [])
            last_exc = None
            for q in attempts:
                if q != cfg["video"]["quality"]:
                    prof = QUALITY_PROFILES[q]
                    cfg["video"].update(width=prof["width"], height=prof["height"], fps=prof["fps"], quality=q)
                    cfg["render"] = {"detail": prof["detail"], "x264_preset": prof["x264_preset"], "crf": prof["crf"]}
                    st.warnings.append(f"rendering fell back to the '{q}' profile ({prof['width']}x{prof['height']} @ {prof['fps']} fps) after: {last_exc}")
                    st.status = Status.PARTIAL_SUCCESS
                renderer = WhiteboardRenderer(cfg, cfg["video"]["width"], cfg["video"]["height"], self.lang)
                t0 = time.time()
                try:
                    render_stats = assemble(renderer, [RenderScene(tl, items) for tl, items in zip(timelines, programs)], str(final_audio), video_path, total, cfg)
                except StageError as exc:
                    last_exc = exc
                    continue
                st.data.update(frames=render_stats.frames, seconds=round(time.time() - t0, 1), profile=cfg["video"]["quality"],
                               resolution=f"{cfg['video']['width']}x{cfg['video']['height']}", fps=cfg["video"]["fps"])
                return
            raise StageError(f"Rendering failed: {last_exc}", Status.PERMANENT_FAILURE)

        self.stage("render", do_render)

        # 9. Captions & side files
        cues = [c for tl in timelines for c in tl.captions]

        def do_outputs(st):
            write_srt(cues, str(self.out / "captions.srt"), self.lang)
            write_vtt(cues, str(self.out / "captions.vtt"))
            (self.out / "transcript.txt").write_text(script_text + "\n", encoding="utf-8")
            if transcript:
                (self.out / "transcription.json").write_text(json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            if (self.tmp / "tts_text.txt").exists():
                shutil.copy(self.tmp / "tts_text.txt", self.out / "tts_input_text.txt")
            (self.out / "scene_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            # Thumbnail: the most detailed fully drawn scene.
            best = max(range(len(programs)), key=lambda i: sum(len(it.ops) for it in programs[i])) if programs else 0
            renderer.render_still(RenderScene(timelines[best], programs[best]), str(self.out / "thumbnail.png"))

        self.stage("outputs", do_outputs)

        # 10. Quality assurance
        report = QualityReport()

        def do_qa(st):
            check_file(report, str(video_path), cfg, total)
            samples = check_frames(report, str(video_path), cfg, plan, timelines)
            check_visuals(report, composed, plan, render_stats, cfg, renderer.captions.missing_chars if renderer.captions else set())
            check_semantics(report, plan, cfg)
            st.data["frame_samples"] = samples
            st.data["failures"] = [c.name for c in report.failures()]
            if not report.passed:
                raise StageError("Quality checks failed: " + ", ".join(c.name for c in report.checks if c.critical and not c.passed))
            if report.failures():
                st.status, st.message = Status.SUCCESS, "passed with warnings: " + ", ".join(c.name for c in report.failures())

        try:
            self.stage("quality_assurance", do_qa)
        finally:
            self.result.quality = report.to_dict()
            (self.out / "quality_report.json").write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        meta.update({
            "status": "SUCCESS",
            "video": {"path": "video.mp4", "width": cfg["video"]["width"], "height": cfg["video"]["height"], "fps": cfg["video"]["fps"],
                      "duration_seconds": round(total, 3), "quality_profile": cfg["video"]["quality"]},
            "scenes": len(plan["scenes"]),
            "scene_types": [s["scene_type"] for s in plan["scenes"]],
            "synchronisation": "word timestamps" if timing.source == "words" else ("exact sentence timing" if timing.source == "exact" else "estimated from sentence lengths and pauses"),
            "warnings": warnings + [w for s in self.result.stages for w in s.warnings],
        })
        (self.out / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        has_partial = any(s.status == Status.PARTIAL_SUCCESS for s in self.result.stages)
        self.result.status = Status.PARTIAL_SUCCESS if has_partial else Status.SUCCESS
        self.result.message = "video generated" + (" (with fallbacks, see metadata)" if has_partial else "")
        self.result.outputs = {p.name: str(p) for p in sorted(self.out.iterdir()) if p.is_file()}
