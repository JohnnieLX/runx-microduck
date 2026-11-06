"""Local Mandarin recognition and one cancellable, bounded command queue."""
import array
from collections import deque
import json
import logging
from pathlib import Path
import sys
import threading
import time

from voice_commands import parse_command,updates_allowed

LOG = logging.getLogger('duck-echo')

# Keep an unknown-speech alternative and negative/quoted forms. A grammar
# containing only positive commands would force unrelated speech into commands.
COMMAND_GRAMMAR = [
    '坐下', '坐下 来', '站起来', '起立', '起来',
    '鸭子 坐下', '鸭子 坐下 来', '鸭子 站起来', '鸭子 起立',
    '小 鸭子 坐下', '小 鸭子 站起来', '请 坐下', '请 站起来',
    '坐下 吧', '站起来 吧',
    '不要 坐下', '不要 站起来', '不要 起来', '不要 起立',
    '别 坐下', '别 站起来', '别 起来', '别 起立',
    '不 坐下', '不 站起来', '不想 坐下', '不想 站起来',
    '不用 坐下', '不用 站起来', '不能 坐下', '不能 站起来',
    '复读 坐下', '复读 站起来', '他说 坐下', '我说 坐下',
    '[unk]',
]


class MandarinRecognizer:
    def __init__(self, model_path, dependencies=None, grammar=True):
        if dependencies:
            sys.path.insert(0, str(dependencies))
        from vosk import KaldiRecognizer, Model, SetLogLevel
        SetLogLevel(-1)
        if not (Path(model_path) / 'am/final.mdl').is_file():
            raise RuntimeError('Chinese speech model is not installed')
        self.model = Model(str(model_path))
        self.recognizer_type = KaldiRecognizer
        # Dialogue needs open-vocabulary speech; the command grammar would force
        # unknown sentences into the sit/stand phrase set.
        phrases = [phrase for phrase in COMMAND_GRAMMAR
                   if all(self.model.vosk_model_find_word(word) >= 0 for word in phrase.split())]
        self.grammar = json.dumps(phrases, ensure_ascii=False) if grammar else None

    def recognize(self, stereo, rate=48000):
        samples = array.array('h')
        samples.frombytes(stereo[:len(stereo) // 4 * 4])
        # Input is already the selected microphone duplicated into stereo by
        # prepare_audio. Keep one channel; Kaldi's model config resamples 48k.
        mono = samples[::2].tobytes()
        recognizer = (self.recognizer_type(self.model, rate, self.grammar)
                      if self.grammar else self.recognizer_type(self.model, rate))
        recognizer.SetWords(True)
        parts = []
        for offset in range(0, len(mono), 8000):
            if recognizer.AcceptWaveform(mono[offset:offset + 8000]):
                parts.append(json.loads(recognizer.Result()))
        parts.append(json.loads(recognizer.FinalResult()))
        scores = [word.get('conf', 0) for part in parts for word in part.get('result', [])]
        return {'text': ' '.join(part.get('text', '') for part in parts).strip(),
                'confidence': min(scores, default=0)}


class LocalAsr:
    """RemoteDialogue ASR adapter over the on-board open-vocabulary Vosk model.

    The model is loaded on the first utterance so daemon start-up never waits for
    it, and a missing model degrades to an empty transcript instead of blocking
    the dialogue worker.
    """

    def __init__(self, model_path, dependencies=None, recognizer_factory=None):
        self.model_path = model_path
        self.dependencies = dependencies
        self._factory = recognizer_factory or (
            lambda: MandarinRecognizer(model_path, dependencies, grammar=False))
        self._recognizer = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._recognizer is None:
                self._recognizer = self._factory()
            return self._recognizer

    def transcribe(self, phrase, cancelled=lambda: False, timeout=20):
        return self._load().recognize(phrase).get('text', '')


class VoiceControl:
    def __init__(self, recognizer_factory, executor, fallback, execute_commands=True,
                 cue=None, ttl=6, clock=time.monotonic, recognize_speech=True,guard=updates_allowed,
                 dialogue=None):
        self.factory, self.executor, self.fallback = recognizer_factory, executor, fallback
        self.execute_commands, self.cue = execute_commands, cue
        self.recognize_speech = recognize_speech
        self.dialogue = dialogue
        self.guard=guard
        self._recognizer = None
        self.ttl, self.clock = ttl, clock
        self.condition = threading.Condition()
        self.queue = deque(maxlen=1)
        self.generation, self.closed = 0, False
        self.ready, self.idle = threading.Event(), threading.Event()
        self.idle.set()
        self.last_result = None
        self.thread = threading.Thread(target=self._run, name='local-speech', daemon=True)
        self.thread.start()

    @property
    def recognizer(self):
        """Ready shared Model owner; consumers create their own Kaldi decoder."""
        return self._recognizer if self.ready.is_set() else None

    def submit(self, pcm):
        with self.condition:
            if not self.guard():
                self.cancel();return
            if self.closed:
                return
            self.queue.append((self.generation, self.clock(), pcm))
            self.idle.clear()
            self.condition.notify()

    def cancel(self):
        with self.condition:
            self.generation += 1
            self.queue.clear()
            if self.dialogue is not None:
                self.dialogue.reset()

    def valid(self, job):
        with self.condition:
            if not self.guard():
                self.cancel();return False
            return not self.closed and job[0] == self.generation and self.clock() - job[1] <= self.ttl

    def _echo(self, job):
        # Hold the short generation check through starting playback so a new
        # R3 press can always cancel first, then stop any already-started echo.
        with self.condition:
            if self.valid(job):
                self.fallback(job[2])

    def _respond(self, job):
        if self.dialogue is None:
            self._echo(job)
            return None
        return self.dialogue.handle(job[2], allowed=lambda: self.valid(job))

    def _run(self):
        try:
            if self.dialogue is not None:
                # Dialogue mode must confirm sit/stand through the remote intent
                # gateway, so the on-board command grammar never runs here.
                recognizer = None
                LOG.info('remote dialogue owns recognition; local command grammar disabled')
            else:
                recognizer = self.factory()
                self._recognizer = recognizer
                LOG.info('local Chinese speech recognition ready; mode=%s',
                         'actions' if self.execute_commands else 'shadow')
        except Exception:
            recognizer = None
            if self.dialogue is not None:
                # Remote dialogue owns recognition; a missing local model is expected.
                LOG.info('local speech model unavailable; remote dialogue handles speech')
            else:
                LOG.exception('speech model unavailable; ordinary echo remains available')
        finally:
            self.ready.set()
        while True:
            with self.condition:
                while not self.queue and not self.closed:
                    self.condition.wait()
                if self.closed:
                    return
                job = self.queue.popleft()
            try:
                if not self.valid(job):
                    continue
                if recognizer is None or not self.recognize_speech:
                    outcome = self._respond(job)
                    if outcome is not None:
                        self.last_result = {'command': None, 'dialogue': outcome}
                    continue
                result = recognizer.recognize(job[2])
                command = parse_command(result.get('text', ''), result.get('confidence', 0))
                if not self.valid(job):
                    continue
                # Do not retain arbitrary conversation transcriptions in logs.
                LOG.info('speech command=%s confidence=%.2f', command or 'none', result.get('confidence', 0))
                if command is None or not self.execute_commands:
                    outcome = self._respond(job)
                    self.last_result = {'command': command, 'shadow': not self.execute_commands}
                    if outcome is not None:
                        self.last_result['dialogue'] = outcome
                    continue
                outcome = self.executor.execute(command, allowed=lambda: self.valid(job))
                self.last_result = {'command': command, **outcome}
                LOG.info('voice action: %s', json.dumps(self.last_result, ensure_ascii=False))
                if self.cue is not None and self.valid(job):
                    try:
                        self.cue(outcome.get('accepted') is True)
                    except Exception:
                        LOG.exception('voice acknowledgement sound unavailable')
            except Exception:
                LOG.exception('speech processing failed; preserving ordinary echo')
                self._echo(job)
            finally:
                with self.condition:
                    if not self.queue:
                        self.idle.set()

    def close(self):
        with self.condition:
            self.closed = True
            self.generation += 1
            self.queue.clear()
            if self.dialogue is not None:
                self.dialogue.reset()
            self.condition.notify_all()
        self.thread.join(timeout=2)
