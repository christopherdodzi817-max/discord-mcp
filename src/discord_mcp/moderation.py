from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class RuleSignal:
    rule_id: str
    label: str
    reason: str


# Common profanity only. Server-specific terms belong in terms_by_rule.
DEFAULT_TERMS: dict[str, tuple[str, ...]] = {
    "1": ("damn", "hell", "shit", "fuck"),
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
    normalized_safe = {normalize_text(phrase) for phrase in safe_phrases}
    signals: list[RuleSignal] = []
    configured = dict(DEFAULT_TERMS)
    for rule_id, terms in terms_by_rule.items():
        configured[rule_id] = (*configured.get(rule_id, ()), *terms)

    for rule_id, terms in configured.items():
        matched = next(
            (term for term in terms if _contains_term(normalized, normalize_text(term))),
            None,
        )
        if matched is not None and normalized not in normalized_safe:
            signals.append(RuleSignal(rule_id, f"Configured term: {matched}", f"Message contains configured term '{matched}'."))
    return signals


def _contains_term(text: str, term: str) -> bool:
    if not term:
        return False
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags=re.UNICODE) is not None


class ActivityTracker:
    """Track recent message activity in a bounded per-user sliding window."""

    def __init__(self, window_seconds: float, message_threshold: int, repeat_threshold: int, mention_threshold: int):
        if window_seconds <= 0 or min(message_threshold, repeat_threshold, mention_threshold) < 1:
            raise ValueError("window and thresholds must be positive")
        self.window_seconds = window_seconds
        self.message_threshold = message_threshold
        self.repeat_threshold = repeat_threshold
        self.mention_threshold = mention_threshold
        self._activity: dict[int, deque[tuple[float, str]]] = {}
        self._max_events = max(message_threshold, repeat_threshold)

    def inspect(self, author_id: int, text: str, mention_count: int, mention_everyone: bool, now: float) -> list[RuleSignal]:
        events = self._activity.setdefault(author_id, deque(maxlen=self._max_events))
        cutoff = now - self.window_seconds
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


def find_invite_signal(text: str, channel_name: str) -> RuleSignal | None:
    if channel_name.casefold() == "server-discovery":
        return None
    if re.search(r"(?:https?://)?(?:www\.)?discord\.gg/[A-Za-z0-9-]+", text, flags=re.IGNORECASE):
        return RuleSignal("6", "Off-channel Discord invite", "Discord invite posted outside server-discovery.")
    return None
