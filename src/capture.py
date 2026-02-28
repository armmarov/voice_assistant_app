import logging
import threading
from collections import deque
from typing import Callable, List, Optional

import numpy as np
import pyaudio
import webrtcvad

from . import config
from .audio import pcm_frames_to_wav

log = logging.getLogger(__name__)


class MicrophoneCapture:
    """
    Continuously reads from the microphone.
    on_wake_word (optional) is called immediately when the wake word is detected,
    before VAD starts collecting the command utterance.

    State machine:
      IDLE      — feeds audio to OpenWakeWord for wake word detection; VAD inactive.
      LISTENING — wake word heard; WebRTC VAD captures an utterance, emits it
                  via on_utterance callback, then returns to IDLE.

    Mute support:
      Call mute() before playback and unmute() after.  While muted the loop
      still reads from the mic (keeps PyAudio buffer drained) but discards every
      frame and aborts any in-progress LISTENING session.

    Frame size:
      PyAudio / VAD: 480 samples (30 ms @ 16 kHz) = 960 bytes
      OpenWakeWord:  accepts any chunk size; buffers internally.
    """

    _PADDING_CHUNKS    = 10   # ring-buffer pre-speech padding: 10 × 30 ms ≈ 300 ms
    _VAD_FRAME_SAMPLES = 480
    _VAD_FRAME_BYTES   = _VAD_FRAME_SAMPLES * 2

    def __init__(self, on_utterance: Callable[[bytes], None], on_wake_word: Optional[Callable] = None):
        self._on_utterance = on_utterance
        self._on_wake_word = on_wake_word
        self._vad = webrtcvad.Vad(config.VAD_AGGRESSIVENESS)
        self._oww = self._init_oww()

        self._pa = pyaudio.PyAudio()
        self._stream: Optional[pyaudio.Stream] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        self._muted = False
        self._mute_lock = threading.Lock()
        self._state = "IDLE"
        self._resume_to_listening = False   # set by resume_listening()
        self._oww_reset_pending = False     # set by unmute(), consumed in capture loop

    # ── setup ────────────────────────────────────────────────────────────────

    def _init_oww(self):
        import openwakeword
        from openwakeword.model import Model

        if config.WAKE_WORD_MODEL_PATH:
            oww = Model(wakeword_models=[config.WAKE_WORD_MODEL_PATH])
            log.info("Wake word engine loaded from %s", config.WAKE_WORD_MODEL_PATH)
        else:
            # Download built-in models if not already present.
            model_file = config.WAKE_WORD_MODEL + "_v0.1.tflite"
            models_dir = __import__("pathlib").Path(openwakeword.__file__).parent / "resources" / "models"
            if not (models_dir / model_file).exists():
                log.info("Downloading OpenWakeWord models (first run) …")
                openwakeword.utils.download_models([config.WAKE_WORD_MODEL + "_v0.1"])
                log.info("Models downloaded.")
            oww = Model(wakeword_models=[config.WAKE_WORD_MODEL])
            log.info("Wake word engine loaded: built-in '%s'", config.WAKE_WORD_MODEL)

        log.info(
            "Wake word threshold: %.2f  (say '%s' to activate)",
            config.WAKE_WORD_THRESHOLD,
            config.WAKE_WORD_MODEL_PATH or config.WAKE_WORD_MODEL,
        )
        return oww

    # ── public mute controls ─────────────────────────────────────────────────

    def mute(self):
        with self._mute_lock:
            self._muted = True
        log.debug("Microphone muted.")

    def unmute(self):
        """Unmute and return to IDLE — used after main pipeline playback."""
        with self._mute_lock:
            self._muted = False
            self._resume_to_listening = False
            self._oww_reset_pending = True
        log.info("Microphone unmuted, state → IDLE.")

    def resume_listening(self):
        """Unmute and return to LISTENING — used after wake word ack playback."""
        with self._mute_lock:
            self._muted = False
            self._resume_to_listening = True
        log.debug("Microphone unmuted, state → LISTENING.")

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        open_kwargs = dict(
            format=pyaudio.paInt16,
            channels=config.MIC_CHANNELS,
            rate=config.MIC_SAMPLE_RATE,
            input=True,
            frames_per_buffer=self._VAD_FRAME_SAMPLES,
        )
        if config.MIC_DEVICE_INDEX >= 0:
            open_kwargs["input_device_index"] = config.MIC_DEVICE_INDEX
        self._stream = self._pa.open(**open_kwargs)
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        log.info(
            "Microphone capture started (wake word active, VAD aggressiveness=%d)",
            config.VAD_AGGRESSIVENESS,
        )

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
        self._pa.terminate()
        log.info("Microphone capture stopped.")

    # ── capture loop ─────────────────────────────────────────────────────────

    def _capture_loop(self):
        silence_limit  = config.VAD_SILENCE_MS      // config.MIC_CHUNK_MS
        min_speech     = config.VAD_MIN_SPEECH_MS   // config.MIC_CHUNK_MS
        timeout_max    = config.WAKE_LISTEN_TIMEOUT_MS // config.MIC_CHUNK_MS

        ring           = deque(maxlen=self._PADDING_CHUNKS)
        voiced: List[bytes] = []
        silence_count  = 0
        timeout_left   = 0
        was_muted      = False
        idle_frames    = 0

        while self._running:
            try:
                frame = self._stream.read(
                    self._VAD_FRAME_SAMPLES, exception_on_overflow=False
                )
            except OSError as exc:
                log.warning("Audio read error: %s", exc)
                continue

            with self._mute_lock:
                muted  = self._muted
                resume = self._resume_to_listening
                if resume:
                    self._resume_to_listening = False
            if muted:
                self._state = "IDLE"
                ring.clear()
                voiced = []
                silence_count = 0
                was_muted = True
                continue
            if was_muted:
                was_muted = False
                log.info("Capture loop resumed, state → %s", "LISTENING" if resume else "IDLE")
            if resume:
                self._state = "LISTENING"
                timeout_left  = timeout_max
                voiced = []
                silence_count = 0
                log.debug("Resumed LISTENING after ack.")

            if self._state == "IDLE":
                idle_frames += 1
                # Log heartbeat every ~30s (1000 frames × 30ms)
                if idle_frames % 1000 == 0:
                    log.info("Idle: listening for wake word … (%ds)", idle_frames * config.MIC_CHUNK_MS // 1000)
                with self._mute_lock:
                    if self._oww_reset_pending:
                        self._oww_reset_pending = False
                        self._oww.reset()
                        # Feed silence frames to flush OWW's internal feature buffer
                        # so stale data from before mute doesn't affect detection.
                        silence = np.zeros(self._VAD_FRAME_SAMPLES, dtype=np.int16)
                        for _ in range(30):  # ~900ms of silence
                            self._oww.predict(silence)
                        log.info("OpenWakeWord model reset and flushed.")
                ring.append(frame)
                pcm = np.frombuffer(frame, dtype=np.int16)
                prediction = self._oww.predict(pcm)
                max_score = max(prediction.values()) if prediction else 0
                # Log best score every ~3s so we can see if OWW is responding
                if idle_frames % 100 == 0 and max_score > 0.01:
                    log.debug("Wake word score: %.4f", max_score)
                if any(score >= config.WAKE_WORD_THRESHOLD for score in prediction.values()):
                    idle_frames = 0
                    log.info("Wake word detected! (score=%.4f)", max_score)
                    self._oww.reset()
                    if self._on_wake_word:
                        self._on_wake_word()
                    voiced        = list(ring)
                    silence_count = 0
                    timeout_left  = timeout_max
                    self._state   = "LISTENING"
                    ring.clear()

            elif self._state == "LISTENING":
                timeout_left -= 1
                if timeout_left <= 0:
                    log.info("Listen timeout — returning to IDLE.")
                    voiced = []
                    ring.clear()
                    silence_count = 0
                    self._state = "IDLE"
                    continue

                voiced.append(frame)
                is_speech = self._vad.is_speech(frame, config.MIC_SAMPLE_RATE)
                if is_speech:
                    if silence_count > 0 or len(voiced) == 1:
                        log.info("VAD: speech detected (voiced frames: %d)", len(voiced))
                    silence_count = 0
                    timeout_left  = timeout_max
                else:
                    silence_count += 1
                    if silence_count >= silence_limit:
                        duration_ms = len(voiced) * config.MIC_CHUNK_MS
                        if len(voiced) >= min_speech:
                            log.info("VAD: utterance complete (%d ms), sending to ASR …", duration_ms)
                            wav = pcm_frames_to_wav(
                                voiced, config.MIC_SAMPLE_RATE, config.MIC_CHANNELS
                            )
                            self._on_utterance(wav)
                            voiced = []
                            ring.clear()
                            silence_count = 0
                            self._state = "IDLE"
                        else:
                            # Too short — stay in LISTENING so user can keep talking.
                            log.info("VAD: utterance too short (%d ms < %d ms), still listening …", duration_ms, config.VAD_MIN_SPEECH_MS)
                            voiced = []
                            silence_count = 0
                            timeout_left = timeout_max
