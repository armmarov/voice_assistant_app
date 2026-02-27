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

    # ── setup ────────────────────────────────────────────────────────────────

    def _init_oww(self):
        from openwakeword.model import Model

        if config.WAKE_WORD_MODEL_PATH:
            oww = Model(wakeword_models=[config.WAKE_WORD_MODEL_PATH])
            log.info("Wake word engine loaded from %s", config.WAKE_WORD_MODEL_PATH)
        else:
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
        log.debug("Microphone unmuted, state → IDLE.")

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
                continue
            if resume:
                self._state = "LISTENING"
                timeout_left  = timeout_max
                voiced = []
                silence_count = 0
                log.debug("Resumed LISTENING after ack.")

            if self._state == "IDLE":
                ring.append(frame)
                pcm = np.frombuffer(frame, dtype=np.int16)
                prediction = self._oww.predict(pcm)
                if any(score >= config.WAKE_WORD_THRESHOLD for score in prediction.values()):
                    log.info("Wake word detected! Listening for command …")
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
                if self._vad.is_speech(frame, config.MIC_SAMPLE_RATE):
                    silence_count = 0
                    timeout_left  = timeout_max
                else:
                    silence_count += 1
                    if silence_count >= silence_limit:
                        if len(voiced) >= min_speech:
                            wav = pcm_frames_to_wav(
                                voiced, config.MIC_SAMPLE_RATE, config.MIC_CHANNELS
                            )
                            self._on_utterance(wav)
                        else:
                            log.debug("Utterance too short, ignored.")
                        voiced = []
                        ring.clear()
                        silence_count = 0
                        self._state = "IDLE"
                        log.debug("Utterance captured → IDLE.")
