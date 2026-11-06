"""Strict remote intent parsing and confirmed sit/stand candidates."""
from dataclasses import dataclass
import json
import re
import threading
import time

from remote_models import validate_reply_text

CONFIRMATIONS = {'确认', '是的', '执行'}
ACTION_NAMES = {'sit': '坐下', 'stand': '站起来'}


@dataclass(frozen=True)
class Chat:
    text: str


@dataclass(frozen=True)
class MotionCandidate:
    action: str


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate intent field')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('invalid JSON constant')


def parse_intent(text):
    if not isinstance(text, str):
        raise ValueError('invalid intent')
    try:
        value = json.loads(text, object_pairs_hook=_unique_object,
                           parse_constant=_invalid_constant)
        if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
            raise ValueError('invalid intent')
        kind = value.get('type')
        if kind == 'chat' and set(value) == {'version', 'type', 'text'}:
            return Chat(validate_reply_text(value['text']))
        if kind == 'motion' and set(value) == {'version', 'type', 'action', 'args'}:
            if value['action'] in ACTION_NAMES and type(value['action']) is str and value['args'] == {} and type(value['args']) is dict:
                return MotionCandidate(value['action'])
    except (ValueError, TypeError, json.JSONDecodeError, RecursionError):
        pass
    raise ValueError('invalid intent')


def _normalize(transcript):
    return re.sub(r'[\s，。！？、,.!?]+', '', transcript)


class IntentGateway:
    def __init__(self, posture, ttl=6, clock=None):
        self.posture = posture
        self.ttl = ttl
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._generation = 0
        self._pending = None

    def _valid(self, generation, allowed):
        with self._lock:
            current = generation == self._generation
        return current and allowed()

    def reply(self, text, allowed=lambda: True):
        with self._lock:
            self._pending = None
            self._generation += 1
            generation = self._generation
        intent = parse_intent(text)
        if not self._valid(generation, allowed):
            return '动作已取消。'
        if isinstance(intent, Chat):
            return intent.text
        created = self.clock()
        with self._lock:
            if generation != self._generation or not allowed():
                return '动作已取消。'
            self._pending = (intent.action, created, generation)
        return f'准备{ACTION_NAMES[intent.action]}，请在6秒内说“确认”。'

    def handle_transcript(self, transcript, allowed=lambda: True):
        if _normalize(transcript) not in CONFIRMATIONS:
            with self._lock:
                self._pending = None
                self._generation += 1
            return None
        with self._lock:
            generation = self._generation
            pending = self._pending
            self._pending = None
        if pending is None:
            return '没有待确认的动作。'
        action, created, candidate_generation = pending
        if candidate_generation != generation or not allowed():
            return '动作已取消。'
        if self.clock() - created >= self.ttl:
            return '动作候选已过期，请重新提出。'
        valid = lambda: self._valid(generation, allowed)
        if not valid():
            return '动作已取消。'
        outcome = self.posture.execute(action, allowed=valid)
        if not valid():
            return '动作已取消。'
        if outcome.get('accepted') is True:
            return f'已{ACTION_NAMES[action]}。'
        reason = str(outcome.get('reason', '机器人状态不允许'))
        return f'动作未执行：{reason}'[:200]

    def cancel(self):
        with self._lock:
            self._pending = None
            self._generation += 1
