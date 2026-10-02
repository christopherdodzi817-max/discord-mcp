from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import re
import unicodedata


@dataclass(frozen=True)
class RuleSignal:
    rule_id: str
    label: str
    reason: str


# Common profanity only. Server-specific terms belong in terms_by_rule.
DEFAULT_TERMS: dict[str, tuple[str, ...]] = {
    "2": ("damn", "hell", "shit", "fuck"),
}


def normalize_text(text: str) -> str:
    """Normalize compatibility characters, case, and runs of whitespace."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def find_term_signals(
    text: str,
    terms_by_rule: dict[str, tuple[str, ...]],
    safe_phrases: tuple[str, ...] = (),
) -> list[RuleSignal]:
    normalized = normalize_text(text)
    is_safe_phrase = normalized in {normalize_text(phrase) for phrase in safe_phrases}
    matches_by_rule: dict[str, list[tuple[str, bool]]] = {}
    explicit_matches: set[tuple[str, str]] = set()

    for rule_id, terms in DEFAULT_TERMS.items():
        for term in terms:
            normalized_term = normalize_text(term)
            if _contains_term(normalized, normalized_term):
                matches_by_rule.setdefault(rule_id, []).append((term, False))

    for rule_id, terms in terms_by_rule.items():
        for term in terms:
            normalized_term = normalize_text(term)
            if _contains_term(normalized, normalized_term):
                matches_by_rule.setdefault(rule_id, []).append((term, True))
                explicit_matches.add((rule_id, normalized_term))

    # A safe phrase has no rule annotation, so only exempt it when it identifies
    # exactly one explicit configured term. Built-in defaults remain active.
    suppressed_match = next(iter(explicit_matches)) if is_safe_phrase and len(explicit_matches) == 1 else None
    signals: list[RuleSignal] = []
    for rule_id, matches in matches_by_rule.items():
        matched = next(
            (
                (term, explicit)
                for term, explicit in matches
                if not (explicit and suppressed_match == (rule_id, normalize_text(term)))
            ),
            None,
        )
        if matched is None:
            continue
        term, _ = matched
        signals.append(RuleSignal(rule_id, f"Configured term: {term}", f"Message contains configured term '{term}'."))
    return signals


def _contains_term(text: str, term: str) -> bool:
    if not term:
        return False
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags=re.UNICODE) is not None


class ActivityTracker:
    """Track recent message activity in a bounded per-user sliding window."""

    def __init__(self, window_seconds: float, message_threshold: int, repeat_threshold: int, mention_threshold: int):
        if not math.isfinite(window_seconds) or window_seconds <= 0 or min(message_threshold, repeat_threshold, mention_threshold) < 1:
            raise ValueError("window and thresholds must be positive")
        self.window_seconds = window_seconds
        self.message_threshold = message_threshold
        self.repeat_threshold = repeat_threshold
        self.mention_threshold = mention_threshold
        self._activity: dict[int | tuple[int, int], deque[tuple[float, str]]] = {}
        self._max_events = max(message_threshold, repeat_threshold)

    def inspect(self, author_id: int | tuple[int, int], text: str, mention_count: int, mention_everyone: bool, now: float) -> list[RuleSignal]:
        cutoff = now - self.window_seconds
        for key, history in list(self._activity.items()):
            if not history or history[-1][0] < cutoff:
                del self._activity[key]
        events = self._activity.setdefault(author_id, deque(maxlen=self._max_events))
        while events and events[0][0] < cutoff:
            events.popleft()
        normalized = normalize_text(text)
        events.append((now, normalized))

        reasons: list[str] = []
        if len(events) >= self.message_threshold:
            reasons.append(f"{len(events)} messages within {self.window_seconds:g} seconds")
        if sum(message == normalized for _, message in events) >= self.repeat_threshold:
            reasons.append("repeated identical messages")
        if mention_everyone:
            reasons.append("mass mention")
        if mention_count >= self.mention_threshold:
            reasons.append(f"{mention_count} mentions in one message")
        if not reasons:
            return []
        return [RuleSignal("3", "Spam or mention flood", "; ".join(reasons) + ".")]


class MentionTracker:
    """Count unique direct-ping messages across channels within a rolling window."""

    def __init__(self, limit: int = 3, window_seconds: float = 3600):
        if limit < 1 or not math.isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError("mention limit and window must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[tuple[int, int, int], dict[int, float]] = {}
        self._warned: dict[tuple[int, int], float] = {}
        self._seen: dict[tuple[int, int], float] = {}

    def record(self, guild_id: int, author_id: int, message_id: int,
               target_ids: list[int], timestamp: float, now: float, *,
               historical: bool = False) -> list[int]:
        cutoff = now - self.window_seconds
        for key, events in list(self._events.items()):
            current = {mid: at for mid, at in events.items() if at > cutoff}
            if current:
                self._events[key] = current
            else:
                del self._events[key]
        self._warned = {key: at for key, at in self._warned.items() if at > cutoff}
        self._seen = {key: at for key, at in self._seen.items() if at > cutoff}
        targets = sorted(set(target_ids) - {author_id})
        seen_key = (guild_id, message_id)
        if timestamp <= cutoff or not targets or seen_key in self._seen:
            return []
        self._seen[seen_key] = timestamp
        triggered = []
        for target_id in targets:
            key = (guild_id, author_id, target_id)
            events = self._events.setdefault(key, {})
            if message_id in events:
                continue
            events[message_id] = timestamp
            self._events[key] = dict(sorted(events.items(), key=lambda item: (item[1], item[0]))[-(self.limit + 1):])
            if len(self._events[key]) > self.limit:
                triggered.append(target_id)
        if historical or (guild_id, author_id) in self._warned:
            return []
        return triggered

    def mark_warned(self, guild_id: int, author_id: int, timestamp: float) -> None:
        key = (guild_id, author_id)
        self._warned[key] = max(timestamp, self._warned.get(key, timestamp))


def find_invite_signal(text: str, channel_name: str) -> RuleSignal | None:
    from .naming import channel_key
    if channel_key(channel_name) == "server-discovery":
        return None
    if re.search(r"(?<![\w.])(?:https?://)?(?:www\.)?discord\.gg/[A-Za-z0-9-]+", text, flags=re.IGNORECASE):
        return RuleSignal("6", "Off-channel Discord invite", "Discord invite posted outside server-discovery.")
    return None

