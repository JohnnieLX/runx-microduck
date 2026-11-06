"""One cancellable ASR → LLM → TTS → playback transaction."""
import logging
import threading

from remote_models import (
    MAX_HISTORY_CHARS, MAX_HISTORY_MESSAGES, TARGET_REPLY_CHARS,
    RemoteCancelled, RequestControl, decode_tts_audio, validate_phrase_pcm,
    validate_reply_text, validate_transcript,
)

LOG = logging.getLogger('duck-echo')
SYSTEM_PROMPT = (
    '你是桌宠鸭子的中文语音助手。用自然、简短的口语回答，'
    f'回答目标长度不超过{TARGET_REPLY_CHARS}个汉字。不要输出动作指令或代码。'
)
INTENT_SYSTEM_PROMPT = (
    '你是桌宠鸭子的中文语音助手。只输出一个JSON对象，不要Markdown或额外文字。'
    '闲聊输出{"version":1,"type":"chat","text":"简短中文回答"}；'
    '坐下或站起请求分别输出{"version":1,"type":"motion","action":"sit","args":{}}'
    '或{"version":1,"type":"motion","action":"stand","args":{}}。'
    '不得输出其他动作、字段、速度、关节、力矩、robotd方法或代码。'
)


class RemoteDialogue:
    def __init__(self, asr, llm, tts, play_reply, intent_gateway=None, timeout=20):
        if timeout <= 0:
            raise ValueError('remote timeout must be positive')
        self.asr, self.llm, self.tts = asr, llm, tts
        self.play_reply = play_reply
        self.intent_gateway = intent_gateway
        self.timeout = timeout
        self._history = []
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._closed = False
        self._generation = 0
        self._active = None

    @property
    def history(self):
        with self._lock:
            return [dict(message) for message in self._history]

    def cancelled(self):
        with self._lock:
            return self._closed or self._cancel_event.is_set()

    def cancel(self):
        with self._lock:
            self._generation += 1
            self._cancel_event.set()
        if self.intent_gateway is not None:
            self.intent_gateway.cancel()

    def reset(self):
        self.cancel()
        with self._lock:
            self._history.clear()

    def close(self):
        with self._lock:
            self._closed = True
        self.cancel()

    def _is_cancelled(self, generation, allowed):
        with self._lock:
            cancelled = (self._closed or self._cancel_event.is_set() or
                         generation != self._generation)
        return cancelled or not allowed()

    @staticmethod
    def _trim(messages):
        messages = messages[-MAX_HISTORY_MESSAGES:]
        while len(messages) > 1 and sum(len(item['content']) for item in messages) > MAX_HISTORY_CHARS:
            messages.pop(0)
        return messages

    def handle(self, phrase, allowed=lambda: True):
        with self._lock:
            if self._closed:
                return {'ok': False, 'cancelled': True}
            if self._active is not None:
                return {'ok': False, 'error': 'busy'}
            generation = self._generation
            self._active = generation
            self._cancel_event.clear()
        cancelled = lambda: self._is_cancelled(generation, allowed)
        control = RequestControl(cancelled)
        stage = 'input'
        try:
            phrase = validate_phrase_pcm(phrase)
            control.check()
            stage = 'asr'
            transcript = validate_transcript(self.asr.transcribe(phrase, cancelled, self.timeout))
            control.check()
            user = {'role': 'user', 'content': transcript}
            remember = True
            direct_reply = None
            if self.intent_gateway is not None:
                stage = 'intent'
                direct_reply = self.intent_gateway.handle_transcript(
                    transcript, allowed=lambda: not cancelled())
                control.check()
            if direct_reply is not None:
                reply = validate_reply_text(direct_reply)
                remember = False
            else:
                with self._lock:
                    context = self._trim(self._history + [user])
                prompt = INTENT_SYSTEM_PROMPT if self.intent_gateway is not None else SYSTEM_PROMPT
                messages = [{'role': 'system', 'content': prompt}] + context
                stage = 'llm'
                reply = validate_reply_text(self.llm.complete(messages, cancelled, self.timeout))
                control.check()
                if self.intent_gateway is not None:
                    stage = 'intent'
                    reply = validate_reply_text(self.intent_gateway.reply(
                        reply, allowed=lambda: not cancelled()))
                    control.check()
            stage = 'tts'
            # HttpTts returns 48 kHz mono; playback needs duplicated stereo.
            audio = decode_tts_audio(self.tts.synthesize(reply, cancelled, self.timeout), channels=1)
            control.check()
            stage = 'playback'
            self.play_reply(audio, allowed=lambda: not cancelled())
            control.check()
            if remember:
                with self._lock:
                    self._history = self._trim(self._history + [
                        user, {'role': 'assistant', 'content': reply},
                    ])
            return {'ok': True, 'transcript_chars': len(transcript), 'reply_chars': len(reply)}
        except RemoteCancelled:
            return {'ok': False, 'cancelled': True}
        except Exception as error:
            # Provider exceptions can contain URLs or authorization material.
            LOG.error('remote dialogue %s failed (%s)', stage, type(error).__name__)
            return {'ok': False, 'error': stage}
        finally:
            with self._lock:
                if self._active == generation:
                    self._active = None
