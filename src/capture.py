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
      IDLE      — feeds audio to wake word engine (OpenWakeWord or Porcupine); VAD inactive.
      LISTENING — wake word heard; WebRTC VAD captures an utterance, emits it
                  via on_utterance callback, then returns to IDLE.

    Wake word engines (set WAKE_WORD_ENGINE in config):
      openwakeword — free, slower, uses TFLite models.
      porcupine    — fast, requires PORCUPINE_ACCESS_KEY from Picovoice.

    Mute support:
      Call mute() before playback and unmute() after.  While muted the loop
      still reads from the mic (keeps PyAudio buffer drained) but discards every
      frame and aborts any in-progress LISTENING session.

    Frame size:
      PyAudio / VAD: 480 samples (30 ms @ 16 kHz) = 960 bytes
      Porcupine:     512 samples — buffered internally.
      OpenWakeWord:  accepts any chunk size; buffers internally.
    """

    _PADDING_CHUNKS    = 10   # ring-buffer pre-speech padding: 10 × 30 ms ≈ 300 ms
    _VAD_FRAME_SAMPLES = 480
    _VAD_FRAME_BYTES   = _VAD_FRAME_SAMPLES * 2

    def __init__(self, on_utterance: Callable[[bytes], None], on_wake_word: Optional[Callable] = None, on_listen_timeout: Optional[Callable] = None):
        self._on_utterance = on_utterance
        self._on_wake_word = on_wake_word
        self._on_listen_timeout = on_listen_timeout
        self._vad = webrtcvad.Vad(config.VAD_AGGRESSIVENESS)

        self._engine = config.WAKE_WORD_ENGINE.lower()
        if self._engine == "porcupine":
            self._porcupine = self._init_porcupine()
            self._oww = None
        else:
            self._oww = self._init_oww()
            self._porcupine = None

        self._pa = pyaudio.PyAudio()
        self._stream: Optional[pyaudio.Stream] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        self._muted = False
        self._mute_lock = threading.Lock()
        self._state = "IDLE"
        self._resume_to_listening = False   # set by resume_listening()
        self._resume_conversation = False   # set by resume_conversation()
        self._ww_reset_pending = False      # set by unmute(), consumed in capture loop
        self._in_conversation = False       # True while in continuous conversation mode

    # ── setup ────────────────────────────────────────────────────────────────

    def _init_oww(self):
        import openwakeword
        from openwakeword.model import Model

        if config.WAKE_WORD_MODEL_PATH:
            oww = Model(wakeword_models=[config.WAKE_WORD_MODEL_PATH])
            log.info("OpenWakeWord engine loaded from %s", config.WAKE_WORD_MODEL_PATH)
        else:
            model_file = config.WAKE_WORD_MODEL + "_v0.1.tflite"
            models_dir = __import__("pathlib").Path(openwakeword.__file__).parent / "resources" / "models"
            if not (models_dir / model_file).exists():
                log.info("Downloading OpenWakeWord models (first run) …")
                openwakeword.utils.download_models([config.WAKE_WORD_MODEL + "_v0.1"])
                log.info("Models downloaded.")
            oww = Model(wakeword_models=[config.WAKE_WORD_MODEL])
            log.info("OpenWakeWord engine loaded: built-in '%s'", config.WAKE_WORD_MODEL)

        log.info(
            "Wake word threshold: %.2f  (say '%s' to activate)",
            config.WAKE_WORD_THRESHOLD,
            config.WAKE_WORD_MODEL_PATH or config.WAKE_WORD_MODEL,
        )
        return oww

    def _init_porcupine(self):
        import pvporcupine

        if not config.PORCUPINE_ACCESS_KEY:
            raise RuntimeError("PORCUPINE_ACCESS_KEY is required when WAKE_WORD_ENGINE=porcupine. "
                               "Get one at https://console.picovoice.ai/")

        kwargs = dict(
            access_key=config.PORCUPINE_ACCESS_KEY,
            sensitivities=[config.PORCUPINE_SENSITIVITY],
        )
        if config.PORCUPINE_KEYWORD_PATH:
            kwargs["keyword_paths"] = [config.PORCUPINE_KEYWORD_PATH]
            label = config.PORCUPINE_KEYWORD_PATH
        else:
            kwargs["keywords"] = [config.PORCUPINE_KEYWORD]
            label = config.PORCUPINE_KEYWORD

        porcupine = pvporcupine.create(**kwargs)
        log.info("Porcupine engine loaded: '%s' (sensitivity=%.2f, frame_length=%d)",
                 label, config.PORCUPINE_SENSITIVITY, porcupine.frame_length)
        return porcupine

    # ── wake word detection ─────────────────────────────────────────────────

    def _detect_wake_word(self, pcm: np.ndarray, idle_frames: int) -> bool:
        """Run one frame through the selected wake word engine. Returns True if detected."""
        if self._engine == "porcupine":
            return self._detect_porcupine(pcm)
        else:
            return self._detect_oww(pcm, idle_frames)

    def _detect_oww(self, pcm: np.ndarray, idle_frames: int) -> bool:
        do_reset = False
        with self._mute_lock:
            if self._ww_reset_pending:
                self._ww_reset_pending = False
                do_reset = True
        if do_reset:
            # Full re-init to guarantee clean state after mute.
            # Done outside the lock since init may be slow.
            self._oww = self._init_oww()
            log.info("OpenWakeWord model re-initialized.")

        prediction = self._oww.predict(pcm)
        max_score = max(prediction.values()) if prediction else 0
        # Log score every ~3s at INFO so we can diagnose detection issues
        if idle_frames % 100 == 0:
            log.info("Wake word score: %.4f (threshold: %.2f)", max_score, config.WAKE_WORD_THRESHOLD)
        if any(score >= config.WAKE_WORD_THRESHOLD for score in prediction.values()):
            log.info("Wake word detected! (score=%.4f)", max_score)
            self._oww.reset()
            return True
        return False

    def _feed_porcupine(self, pcm: np.ndarray) -> bool:
        """Feed audio to Porcupine and return True if keyword detected.

        Must be called continuously (even during mute) to keep Porcupine's
        internal sliding window in sync with the live audio stream.
        """
        if not hasattr(self, '_ppn_buf'):
            self._ppn_buf = np.array([], dtype=np.int16)
            self._ppn_process_count = 0

        self._ppn_buf = np.concatenate([self._ppn_buf, pcm])
        fl = self._porcupine.frame_length
        detected = False
        while len(self._ppn_buf) >= fl:
            frame_to_process = self._ppn_buf[:fl]
            keyword_index = self._porcupine.process(frame_to_process)
            self._ppn_buf = self._ppn_buf[fl:]
            self._ppn_process_count = getattr(self, '_ppn_process_count', 0) + 1
            if keyword_index >= 0:
                log.info("Wake word detected! (porcupine keyword_index=%d, after %d frames)",
                         keyword_index, self._ppn_process_count)
                detected = True
            # Log diagnostic every ~3s (512 samples @ 16kHz ≈ 32ms per frame → ~94 frames/3s)
            elif self._ppn_process_count % 94 == 0:
                rms = int(np.sqrt(np.mean(frame_to_process.astype(np.int32) ** 2)))
                log.info("Porcupine: %d frames processed, keyword_index=%d, rms=%d",
                         self._ppn_process_count, keyword_index, rms)
        return detected

    def _detect_porcupine(self, pcm: np.ndarray) -> bool:
        # Porcupine expects exactly frame_length samples (typically 512 for 16kHz).
        # VAD reads 480 samples per frame, so we buffer until we have enough.
        # Reset flag is consumed but no re-creation needed — Porcupine is kept
        # warm by continuous feeding during mute (see _capture_loop).
        with self._mute_lock:
            if self._ww_reset_pending:
                self._ww_reset_pending = False
                self._ppn_buf = np.array([], dtype=np.int16)
                self._ppn_process_count = 0
                log.info("Porcupine buffer cleared after unmute.")
        return self._feed_porcupine(pcm)

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
            self._ww_reset_pending = True
        log.info("Microphone unmuted, state → IDLE.")

    def resume_listening(self):
        """Unmute and return to LISTENING — used after wake word ack playback."""
        with self._mute_lock:
            self._muted = False
            self._resume_to_listening = True
        log.info("Microphone unmuted, state → LISTENING.")

    def resume_conversation(self):
        """Unmute and return to LISTENING with conversation timeout — used after TTS reply."""
        with self._mute_lock:
            self._muted = False
            self._resume_conversation = True
        log.info("Microphone unmuted, state → LISTENING (conversation mode).")

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
        if self._porcupine:
            self._porcupine.delete()
        log.info("Microphone capture stopped.")

    # ── capture loop ─────────────────────────────────────────────────────────

    def _capture_loop(self):
        silence_limit    = config.VAD_SILENCE_MS      // config.MIC_CHUNK_MS
        min_speech       = config.VAD_MIN_SPEECH_MS   // config.MIC_CHUNK_MS
        timeout_wake     = config.WAKE_LISTEN_TIMEOUT_MS    // config.MIC_CHUNK_MS
        timeout_convo    = config.CONVERSATION_TIMEOUT_MS   // config.MIC_CHUNK_MS

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
                muted       = self._muted
                resume      = self._resume_to_listening
                resume_conv = self._resume_conversation
                if resume:
                    self._resume_to_listening = False
                if resume_conv:
                    self._resume_conversation = False
            if muted:
                self._state = "IDLE"
                ring.clear()
                voiced = []
                silence_count = 0
                was_muted = True
                # Keep Porcupine's internal state warm by feeding it audio
                # continuously, even while muted.  Detections are ignored.
                if self._engine == "porcupine":
                    pcm = np.frombuffer(frame, dtype=np.int16)
                    self._feed_porcupine(pcm)
                continue

            if was_muted:
                was_muted = False
                if resume_conv:
                    log.info("Capture loop resumed, state → LISTENING (conversation mode)")
                elif resume:
                    log.info("Capture loop resumed, state → LISTENING")
                else:
                    log.info("Capture loop resumed, state → IDLE")
            if resume or resume_conv:
                self._state = "LISTENING"
                self._in_conversation = resume_conv or self._in_conversation
                timeout_left = timeout_convo if self._in_conversation else timeout_wake
                voiced = []
                silence_count = 0
                log.debug("Resumed LISTENING (conversation=%s, timeout=%ds).",
                          self._in_conversation, timeout_left * config.MIC_CHUNK_MS // 1000)

            if self._state == "IDLE":
                idle_frames += 1
                ring.append(frame)
                pcm = np.frombuffer(frame, dtype=np.int16)

                # Log heartbeat every ~30s with audio level
                if idle_frames % 1000 == 0:
                    rms = int(np.sqrt(np.mean(pcm.astype(np.int32) ** 2)))
                    log.info("Idle: listening for wake word … (%ds, rms=%d)",
                             idle_frames * config.MIC_CHUNK_MS // 1000, rms)

                detected = self._detect_wake_word(pcm, idle_frames)

                if detected:
                    idle_frames = 0
                    self._in_conversation = False
                    if self._on_wake_word:
                        self._on_wake_word()
                    voiced        = list(ring)
                    silence_count = 0
                    timeout_left  = timeout_wake
                    self._state   = "LISTENING"
                    ring.clear()

            elif self._state == "LISTENING":
                timeout_left -= 1
                if timeout_left <= 0:
                    if self._in_conversation:
                        log.info("Conversation timeout (%ds) — returning to IDLE.",
                                 config.CONVERSATION_TIMEOUT_MS // 1000)
                    else:
                        log.info("Listen timeout — returning to IDLE.")
                    voiced = []
                    ring.clear()
                    silence_count = 0
                    self._state = "IDLE"
                    self._in_conversation = False
                    if self._on_listen_timeout:
                        self._on_listen_timeout()
                    continue

                voiced.append(frame)
                is_speech = self._vad.is_speech(frame, config.MIC_SAMPLE_RATE)
                if is_speech:
                    if silence_count > 0 or len(voiced) == 1:
                        log.info("VAD: speech detected (voiced frames: %d)", len(voiced))
                    silence_count = 0
                    timeout_left  = timeout_convo if self._in_conversation else timeout_wake
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
                            # In conversation mode, stay LISTENING for next question.
                            # Otherwise return to IDLE (wake word required).
                            if not self._in_conversation:
                                self._state = "IDLE"
                        else:
                            # Too short — stay in LISTENING so user can keep talking.
                            log.info("VAD: utterance too short (%d ms < %d ms), still listening …", duration_ms, config.VAD_MIN_SPEECH_MS)
                            voiced = []
                            silence_count = 0
                            timeout_left = timeout_convo if self._in_conversation else timeout_wake
