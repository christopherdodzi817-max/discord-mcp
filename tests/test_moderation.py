from discord_mcp.moderation import ActivityTracker, find_invite_signal, find_term_signals


def test_term_matching_uses_normalized_whole_tokens():
    flags = find_term_signals("BADWORD!", {"1": ("badword",)})
    assert [flag.rule_id for flag in flags] == ["1"]


def test_term_matching_does_not_flag_benign_substrings():
    assert find_term_signals("notbadwordish", {"1": ("badword",)}) == []


def test_term_matching_normalizes_unicode_and_case():
    assert [flag.rule_id for flag in find_term_signals("ＢＡＤＷＯＲＤ", {"1": ("badword",)})] == ["1"]


def test_safe_phrase_suppresses_only_the_matching_term_signal():
    assert find_term_signals(
        "quoting badword for context", {"1": ("badword",)}, ("quoting badword for context",)
    ) == []
    assert [flag.rule_id for flag in find_term_signals("badword", {"1": ("badword",)}, ("quoting badword for context",))] == ["1"]


def test_duplicate_burst_flags_rule_three_after_threshold():
    tracker = ActivityTracker(10, 10, 3, 8)
    assert tracker.inspect(7, "same text", 0, False, 0) == []
    assert tracker.inspect(7, "same text", 0, False, 1) == []
    assert "3" in [flag.rule_id for flag in tracker.inspect(7, "same text", 0, False, 2)]


def test_old_activity_expires_from_spam_window():
    tracker = ActivityTracker(10, 5, 3, 8)
    for second in range(4):
        tracker.inspect(7, f"message {second}", 0, False, float(second))
    assert tracker.inspect(7, "new message", 0, False, 20) == []


def test_message_flood_flags_rule_three_at_threshold():
    tracker = ActivityTracker(10, 5, 10, 8)
    for second in range(4):
        assert tracker.inspect(7, f"message {second}", 0, False, float(second)) == []
    assert "3" in [flag.rule_id for flag in tracker.inspect(7, "message 4", 0, False, 4)]


def test_mass_mentions_flag_rule_three():
    tracker = ActivityTracker(10, 10, 3, 8)
    assert "3" in [flag.rule_id for flag in tracker.inspect(7, "hello", 0, True, 0)]


def test_high_mention_count_flags_rule_three():
    tracker = ActivityTracker(10, 10, 3, 8)
    assert "3" in [flag.rule_id for flag in tracker.inspect(7, "hello", 8, False, 0)]


def test_invites_outside_discovery_flag_rule_six():
    flag = find_invite_signal("join https://discord.gg/example", "general")
    assert flag is not None and flag.rule_id == "6"


def test_invite_in_discovery_channel_is_not_flagged():
    assert find_invite_signal("join https://discord.gg/example", "server-discovery") is None

def test_safe_phrase_with_multiple_matching_terms_suppresses_none():
    flags = find_term_signals(
        "quoting badword for scam context",
        {"1": ("badword",), "4": ("scam",)},
        ("quoting badword for scam context",),
    )
    assert [flag.rule_id for flag in flags] == ["1", "4"]


def test_default_profanity_uses_rule_two():
    assert [flag.rule_id for flag in find_term_signals("shit", {})] == ["2"]


def test_invite_domain_embedded_in_word_is_not_flagged():
    assert find_invite_signal("notdiscord.gg/example", "general") is None


def test_safe_phrase_suppresses_explicit_term_not_default_profanity():
    flags = find_term_signals("shit is badword", {"1": ("badword",)}, ("shit is badword",))
    assert [flag.rule_id for flag in flags] == ["2"]


def test_safe_phrase_matching_multiple_configured_terms_suppresses_none():
    flags = find_term_signals(
        "badword and scam", {"1": ("badword",), "4": ("scam",)}, ("badword and scam",)
    )
    assert [flag.rule_id for flag in flags] == ["1", "4"]


def test_activity_is_scoped_by_guild_and_user():
    tracker = ActivityTracker(10, 3, 3, 8)
    tracker.inspect((1, 7), "same", 0, False, 0)
    tracker.inspect((1, 7), "same", 0, False, 1)
    assert tracker.inspect((2, 7), "same", 0, False, 2) == []
    assert tracker.inspect((1, 7), "same", 0, False, 2)


def test_inactive_users_are_evicted():
    tracker = ActivityTracker(10, 3, 3, 8)
    tracker.inspect((1, 7), "hello", 0, False, 0)
    tracker.inspect((1, 8), "hello", 0, False, 11)
    assert (1, 7) not in tracker._activity
