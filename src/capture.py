import logging
import struct
import threading
from collections import deque
from typing import Callable, List, Optional

import pyaudio
import pvporcupine
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
      IDLE      — feeds audio to Porcupine for wake word detection; VAD inactive.
      LISTENING — wake word heard; WebRTC VAD captures an utterance, emits it
                  via on_utterance callback, then returns to IDLE.

    Mute support:
      Call mute() before playback and unmute() after.  While muted the loop
      still reads from the mic (keeps PyAudio buffer drained) but discards every
      frame and aborts any in-progress LISTENING session.

    Frame-size reconciliation:
      Porcupine: frame_length samples (512 @ 16 kHz) = 1024 bytes
      WebRTC VAD: 480 samples (30 ms @ 16 kHz)       =  960 bytes
      Solution: read 1024 bytes per iteration; feed all to Porcupine, feed first
      960 bytes to VAD.
    """

    _PADDING_CHUNKS    = 10   # ring-buffer pre-speech padding: 10 × 30 ms ≈ 300 ms
    _VAD_FRAME_SAMPLES = 480
    _VAD_FRAME_BYTES   = _VAD_FRAME_SAMPLES * 2

    def __init__(self, on_utterance: Callable[[bytes], None], on_wake_word: Optional[Callable] = None):
        self._on_utterance = on_utterance
        self._on_wake_word = on_wake_word
        self._vad = webrtcvad.Vad(config.VAD_AGGRESSIVENESS)
        self._porcupine = self._init_porcupine()
        self._porcupine_frame_samples = self._porcupine.frame_length

        self._pa = pyaudio.PyAudio()
        self._stream: Optional[pyaudio.Stream] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        self._muted = False
        self._mute_lock = threading.Lock()
        self._state = "IDLE"
        self._resume_to_listening = False   # set by resume_listening()

    # ── setup ────────────────────────────────────────────────────────────────

    def _init_porcupine(self) -> pvporcupine.Porcupine:
        if not config.PORCUPINE_ACCESS_KEY:
            raise RuntimeError(
                "PORCUPINE_ACCESS_KEY is not set. "
                "Register at https://console.picovoice.ai/ and set the env var."
            )
        if config.WAKE_WORD_MODEL_PATH:
            porcupine = pvporcupine.create(
                access_key=config.PORCUPINE_ACCESS_KEY,
                keyword_paths=[config.WAKE_WORD_MODEL_PATH],
                sensitivities=[config.WAKE_WORD_SENSITIVITY],
            )
            log.info("Wake word engine loaded from %s", config.WAKE_WORD_MODEL_PATH)
        else:
            porcupine = pvporcupine.create(
                access_key=config.PORCUPINE_ACCESS_KEY,
                keywords=["porcupine"],
                sensitivities=[config.WAKE_WORD_SENSITIVITY],
            )
            log.warning(
                "WAKE_WORD_MODEL_PATH not set — using built-in 'porcupine' keyword. "
                "Say 'porcupine' to activate."
            )
        return porcupine

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
            frames_per_buffer=self._porcupine_frame_samples,
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
        self._porcupine.delete()
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
                    self._porcupine_frame_samples, exception_on_overflow=False
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

            pcm_int16 = struct.unpack_from(f"{self._porcupine_frame_samples}h", frame)
            vad_frame = frame[: self._VAD_FRAME_BYTES]

            if self._state == "IDLE":
                ring.append(vad_frame)
                if self._porcupine.process(pcm_int16) >= 0:
                    log.info("Wake word detected! Listening for command …")
                    if self._on_wake_word:
                        self._on_wake_word()
                    voiced       = list(ring)
                    silence_count = 0
                    timeout_left  = timeout_max
                    self._state  = "LISTENING"
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

                voiced.append(vad_frame)
                if self._vad.is_speech(vad_frame, config.MIC_SAMPLE_RATE):
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
