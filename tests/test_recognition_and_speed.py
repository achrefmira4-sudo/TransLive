"""Tests for faster end-of-phrase detection, quiet-speech pickup, and decoding.

Covers:
  1. Faster subtitles: tighter pause presets, reverb-tail immunity, click
     immunity during pauses, progressive clause splitting, and tail trimming.
  2. Better recognition: quiet-speech VAD sensitivity, DC-offset removal,
     transient-resistant active-RMS normalization, beam search + temperature
     fallback + Silero VAD tuning, conversational context carry-over, prompt
     echo protection, and dominant-language recovery.

Run:  python tests/test_recognition_and_speed.py
  or: pytest tests/test_recognition_and_speed.py -q
"""
from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.translator import (
    FALLBACK_TEMPERATURES,
    VAD_PARAMETERS,
    Translator,
    normalize_audio,
)
from app.vad import Segmenter

SR = 16000


def _speech(seconds: float, amp: float = 0.20, dc: float = 0.0) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    sig = (
        0.55 * np.sin(2 * np.pi * 175 * t)
        + 0.30 * np.sin(2 * np.pi * 390 * t)
        + 0.15 * np.random.default_rng(7).standard_normal(len(t))
    )
    return (sig * amp + dc).astype(np.float32)


def _silence(seconds: float, amp: float = 0.0004, dc: float = 0.0) -> np.ndarray:
    n = int(SR * seconds)
    return (np.random.default_rng(11).standard_normal(n) * amp + dc).astype(np.float32)


def _measure_end_latency_ms(speed: str) -> float:
    seg = Segmenter.from_speed(speed)
    seg.push(_silence(0.3))
    seg.push(_speech(1.0))
    step = int(SR * 0.02)  # 20 ms
    silent_chunk = _silence(0.02)
    elapsed_ms = 0.0
    for _ in range(100):
        elapsed_ms += 20.0
        if seg.push(silent_chunk):
            return elapsed_ms
    raise AssertionError(f"{speed} never emitted utterance")


def test_phrase_end_detected_sooner():
    fast_ms = _measure_end_latency_ms("fast")
    bal_ms = _measure_end_latency_ms("balanced")
    acc_ms = _measure_end_latency_ms("accurate")

    assert fast_ms <= 260, f"fast preset too slow: {fast_ms} ms"
    assert bal_ms <= 360, f"balanced preset too slow: {bal_ms} ms"
    assert acc_ms <= 560, f"accurate preset too slow: {acc_ms} ms"
    assert fast_ms < bal_ms < acc_ms, (fast_ms, bal_ms, acc_ms)


def test_loud_speech_with_reverb_tail_ends_promptly():
    """Room reverb / comfort noise (~0.004 RMS) after loud speech must not
    hold the phrase open when onset_threshold is low for quiet speakers."""
    seg = Segmenter.from_speed("balanced")
    seg.push(_silence(0.3, amp=0.0003))
    seg.push(_speech(1.2, amp=0.22))

    # Tail at ~0.004 RMS: above quiet onset (0.0018) but far below speech RMS.
    reverb_tail = _silence(0.5, amp=0.004)
    out = seg.push(reverb_tail)
    assert len(out) == 1, f"expected phrase to end over reverb tail, got {len(out)}"


def test_single_click_during_pause_does_not_reset_timer():
    """An isolated 20 ms click mid-pause must not force another full wait."""
    seg = Segmenter.from_speed("balanced")
    seg.push(_silence(0.3))
    seg.push(_speech(0.8))

    # 160 ms of pause, then a single 20 ms transient click, then 180 ms pause.
    assert seg.push(_silence(0.16)) == []
    assert seg.push(_speech(0.02, amp=0.15)) == []
    out = seg.push(_silence(0.18))
    assert len(out) == 1, "20 ms click reset the end-of-phrase pause timer"


def test_genuine_resumed_speech_bridges_short_pause():
    """Speaking again (>40 ms) before end_frames keeps words in one phrase."""
    seg = Segmenter.from_speed("balanced")
    out = []
    out += seg.push(_silence(0.3))
    out += seg.push(_speech(0.5))
    out += seg.push(_silence(0.16))  # shorter than ~320 ms balanced pause
    out += seg.push(_speech(0.5))
    out += seg.push(_silence(0.5))
    assert len(out) == 1, f"expected 1 combined phrase, got {len(out)}"
    assert len(out[0]) / SR >= 1.1


def test_progressive_pause_splits_long_clause_at_breath():
    """After >50% of max_segment_s, a brief ~200 ms breath splits cleanly."""
    seg = Segmenter.from_speed("balanced")  # max 9 s, base pause 320 ms
    seg.push(_silence(0.3))
    assert seg.push(_speech(7.2)) == []
    # A 220 ms breath pause is shorter than 320 ms, but at 7.2 s the progressive
    # threshold has tightened enough to finalize the clause cleanly.
    out = seg.push(_silence(0.22))
    assert len(out) == 1, f"expected breath split on long clause, got {len(out)}"


def test_trailing_silence_trimmed():
    seg = Segmenter.from_speed("accurate")  # ~520 ms pause detection
    seg.push(_silence(0.2))
    seg.push(_speech(1.0))
    out = seg.push(_silence(1.0))
    assert len(out) == 1
    # 1.0 s speech + <=0.26 s preroll + ~0.10 s kept tail < 1.5 s
    dur = len(out[0]) / SR
    assert 1.0 <= dur <= 1.5, f"excess trailing silence not trimmed: {dur:.2f}s"


def test_quiet_speech_picked_up_by_segmenter():
    """Quiet speech (~0.003 RMS, below the old 0.006 floor) must be captured,
    even when the input carries a small DC offset."""
    seg = Segmenter.from_speed("balanced")
    out = []
    out += seg.push(_silence(0.4, amp=0.0003, dc=0.01))
    out += seg.push(_speech(1.0, amp=0.005, dc=0.01))  # ~0.0032 RMS
    out += seg.push(_silence(0.5, amp=0.0003, dc=0.01))
    assert len(out) == 1, f"quiet speech was missed (got {len(out)} segments)"

    # Pure silence with the same DC offset still produces nothing.
    seg2 = Segmenter.from_speed("balanced")
    assert seg2.push(_silence(3.0, amp=0.0004, dc=0.01)) == []


def test_normalize_audio_boosts_quiet_speech_and_ignores_clicks():
    # 1. Silence is rejected before hitting Whisper.
    assert normalize_audio(_silence(1.0, amp=0.0004)) is None

    # 2. Very quiet speech (~0.003 RMS) is boosted close to target_rms (~0.14),
    #    whereas the old peak cap left it below 0.06 RMS.
    quiet = _speech(1.2, amp=0.0045, dc=0.015)
    norm_quiet = normalize_audio(quiet)
    assert norm_quiet is not None
    rms_after = float(np.sqrt(np.mean(norm_quiet ** 2)))
    assert 0.09 <= rms_after <= 0.22, f"quiet RMS after AGC = {rms_after:.4f}"
    assert abs(float(np.mean(norm_quiet))) < 0.01

    # 3. Quiet speech + a single loud click (peak 0.50) still boosts the speech
    #    and soft-limits the click within [-1, 1].
    with_click = _speech(1.2, amp=0.0045)
    with_click[500:510] = 0.50
    norm_click = normalize_audio(with_click)
    assert norm_click is not None
    speech_part_rms = float(np.sqrt(np.mean(norm_click[2000:] ** 2)))
    assert speech_part_rms >= 0.09, (
        f"click defeated quiet-speech boost: RMS={speech_part_rms:.4f}"
    )
    assert float(np.max(np.abs(norm_click))) <= 1.0


def _fake_segment(text: str, logprob: float = -0.45, no_speech: float = 0.02,
                  compression: float = 1.1):
    return SimpleNamespace(
        text=text,
        avg_logprob=logprob,
        no_speech_prob=no_speech,
        compression_ratio=compression,
    )


def _fake_info(lang: str = "fr", prob: float = 0.94, vad_dur: float = 1.1):
    return SimpleNamespace(
        language=lang,
        language_probability=prob,
        duration_after_vad=vad_dur,
    )


def test_translator_decoding_context_and_prior():
    tr = Translator(model_size="base", device="cpu")
    fake_model = MagicMock()
    tr.model = fake_model
    tr.actual_device = "cpu"

    calls = []

    def side_effect(audio, **kwargs):
        calls.append(kwargs)
        if kwargs.get("language") == "ar":
            return [_fake_segment("Welcome back everyone.", -0.5)], _fake_info("ar", 1.0)
        if len(calls) == 1:
            return [_fake_segment("We are deploying the server now.", -0.4)], _fake_info("ar", 0.95)
        if len(calls) == 2:
            return [_fake_segment("Please check the logs carefully.", -0.45)], _fake_info("ar", 0.92)
        # 3rd utterance: short clip misdetected as Japanese with low confidence.
        return [_fake_segment("Thank you.", -1.4)], _fake_info("ja", 0.35)

    fake_model.transcribe.side_effect = side_effect

    r1 = tr.process(_speech(1.5, amp=0.05))
    assert r1 is not None and r1.english == "We are deploying the server now."
    assert calls[0]["beam_size"] == 3
    assert calls[0]["temperature"] == FALLBACK_TEMPERATURES
    assert calls[0]["vad_parameters"] == VAD_PARAMETERS
    assert calls[0]["initial_prompt"] is None

    # 2nd utterance receives bounded context from the 1st high-confidence line.
    r2 = tr.process(_speech(1.5, amp=0.05))
    assert r2 is not None and r2.english == "Please check the logs carefully."
    assert "deploying the server" in (calls[1]["initial_prompt"] or "")

    # 3rd utterance: misdetected hallucination on short clip retries with
    # dominant session language ('ar') and recovers the real translation.
    r3 = tr.process(_speech(1.2, amp=0.05))
    assert r3 is not None
    assert r3.retried is True
    assert r3.language == "ar"
    assert r3.english == "Welcome back everyone."

    # CUDA selects beam_size=5 automatically unless overridden.
    tr.actual_device = "cuda"
    assert tr._effective_beam_size() == 5

    # reset_session clears conversational context and language prior.
    tr.reset_session()
    assert tr._active_prompt() is None
    assert tr._dominant_language() is None


def test_translator_skips_decode_when_vad_finds_no_speech():
    tr = Translator(model_size="base", device="cpu")
    fake_model = MagicMock()
    tr.model = fake_model

    consumed = {"iterated": False}

    def lazy_segments():
        consumed["iterated"] = True
        yield _fake_segment("Should never be decoded")

    fake_model.transcribe.return_value = (lazy_segments(), _fake_info(vad_dur=0.0))
    res = tr.process(_speech(1.0, amp=0.05))
    assert res is None
    assert consumed["iterated"] is False, "lazy generator should not run on 0s VAD"


def test_translator_prompt_echo_guard():
    tr = Translator(model_size="base", device="cpu")
    fake_model = MagicMock()
    tr.model = fake_model
    tr._context.append("Let us meet at the north gate.")
    tr._context_ts = time.monotonic()

    prompts_seen = []

    def side_effect(audio, **kwargs):
        prompts_seen.append(kwargs.get("initial_prompt"))
        if kwargs.get("initial_prompt"):
            # Simulate Whisper echoing the prompt with low confidence.
            return [_fake_segment("meet at the north gate", logprob=-1.2)], _fake_info("fr", 0.9)
        return [_fake_segment("Yes, right away.", logprob=-0.5)], _fake_info("fr", 0.9)

    fake_model.transcribe.side_effect = side_effect
    res = tr.process(_speech(1.2, amp=0.05))
    assert res is not None
    assert res.english == "Yes, right away."
    assert len(prompts_seen) == 2 and prompts_seen[0] is not None and prompts_seen[1] is None


def _run_all() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
