"""Meta MMS-TTS (VITS) adapter via Hugging Face Transformers (local inference).

Covers Urdu (facebook/mms-tts-urd-script_arabic) and Pashto (configurable model
id, default facebook/mms-tts-pbt). Model weights are CC-BY-NC-4.0, i.e.
NON-COMMERCIAL; this is recorded in each video's metadata. Disable with
voice.allow_noncommercial_models: false. NOTE: not exercised in the
development container (Hugging Face was unreachable).
"""

from __future__ import annotations

from .base import TTSError, TTSProvider


class MMSProvider(TTSProvider):
    name = "mms"
    license = "CC-BY-NC-4.0 (Meta MMS-TTS weights)"
    commercial_use = "no (non-commercial licence)"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._models: dict[str, tuple] = {}

    @staticmethod
    def _deps():
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
            return True
        except Exception:
            return False

    def describe(self, language: str) -> dict:
        d = super().describe(language)
        d["model"] = getattr(self, "loaded_model", None)
        return d

    def is_available(self, language: str) -> bool:
        if not self.cfg["voice"].get("allow_noncommercial_models", False):
            return False
        return bool(self.cfg["voice"]["mms_models"].get(language)) and self._deps()

    def synthesize(self, text: str, language: str, output_path: str) -> str:
        ids = [m.strip() for m in (self.cfg["voice"]["mms_models"].get(language) or "").split(",") if m.strip()]
        if not ids:
            raise TTSError(f"No MMS model configured for {language}")
        import numpy as np
        import torch
        from transformers import AutoTokenizer, VitsModel

        # Several candidate model ids may be configured (comma separated); the first that loads is used.
        errors = []
        model_id = None
        for mid in ids:
            if mid not in self._models:
                try:
                    self._models[mid] = (VitsModel.from_pretrained(mid), AutoTokenizer.from_pretrained(mid))
                except Exception as exc:
                    errors.append(f"{mid}: {exc}")
                    continue
            model_id = mid
            break
        if model_id is None:
            raise TTSError("Cannot load any MMS model: " + "; ".join(errors)[:500])
        self.loaded_model = model_id
        model, tok = self._models[model_id]
        if getattr(tok, "is_uroman", False):
            try:
                import uroman as ur  # type: ignore
                text = ur.Uroman().romanize_string(text)
            except Exception as exc:
                raise TTSError(f"{model_id} needs uroman romanisation, which is not installed") from exc
        inputs = tok(text, return_tensors="pt")
        if inputs["input_ids"].shape[-1] == 0:
            raise TTSError("Text contains no characters known to the MMS tokenizer")
        with torch.no_grad():
            wav = model(**inputs).waveform[0].cpu().numpy()
        import wave

        pcm = (np.clip(wav, -1, 1) * 32767).astype("<i2")
        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(model.config.sampling_rate))
            wf.writeframes(pcm.tobytes())
        return output_path
