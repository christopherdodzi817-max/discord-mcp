from discord_mcp import moderation


def tracker():
    assert hasattr(moderation, 'MentionTracker'), 'The hourly mention tracker has not been implemented'
    return moderation.MentionTracker()


def test_fourth_tag_of_same_person_triggers():
    subject = tracker()
    for message_id in range(1, 4):
        assert subject.record(1, 4, message_id, [7], message_id, 10) == []
    assert subject.record(1, 4, 4, [7], 4, 10) == [7]


def test_sender_cooldown_avoids_repeated_warnings():
    subject = tracker()
    for message_id in range(1, 5):
        subject.record(1, 4, message_id, [7], message_id, 10)
    subject.mark_warned(1, 4, 10)
    assert subject.record(1, 4, 5, [7, 8], 11, 11) == []
    for message_id in range(6, 9):
        assert subject.record(1, 4, message_id, [8], 12, 12) == []


def test_duplicate_message_and_targets_count_once():
    subject = tracker()
    for message_id in range(1, 4):
        assert subject.record(1, 4, message_id, [7, 7], message_id, 10) == []
        assert subject.record(1, 4, message_id, [7], message_id, 10) == []
    assert subject.record(1, 4, 4, [7], 4, 10) == [7]


def test_expired_tags_do_not_count():
    subject = tracker()
    for message_id in range(1, 4):
        subject.record(1, 4, message_id, [7], 1, 1)
    assert subject.record(1, 4, 4, [7], 3601, 3601) == []


def test_targets_senders_and_guilds_have_separate_counts():
    subject = tracker()
    for message_id in range(1, 4):
        subject.record(1, 4, message_id, [7], 1, 1)
    assert subject.record(1, 4, 4, [8], 2, 2) == []
    assert subject.record(1, 5, 5, [7], 2, 2) == []
    assert subject.record(2, 4, 6, [7], 2, 2) == []
    assert subject.record(1, 4, 7, [7], 2, 2) == [7]


def test_historical_messages_never_warn_but_count_for_next_message():
    subject = tracker()
    for message_id in range(1, 5):
        assert subject.record(1, 4, message_id, [7], 1, 10, historical=True) == []
    assert subject.record(1, 4, 5, [7], 11, 11) == [7]


def test_self_tags_never_count():
    subject = tracker()
    for message_id in range(1, 8):
        assert subject.record(1, 4, message_id, [4], 1, 1) == []


def test_prunes_expired_entries_and_cooldowns():
    subject = tracker()
    subject.record(1, 4, 1, [7], 1, 1)
    subject.mark_warned(1, 4, 1)
    subject.record(1, 5, 2, [8], 4000, 4000)
    assert (1, 4, 7) not in subject._events
    assert (1, 4) not in subject._warned


def test_historical_order_does_not_displace_latest_events():
    subject = tracker()
    for message_id in range(5, 0, -1):
        subject.record(1, 4, message_id, [7], message_id, 10, historical=True)
    assert list(subject._events[(1, 4, 7)]) == [2, 3, 4, 5]


def test_replayed_old_id_after_history_recovery_cannot_warn():
    subject = tracker()
    for message_id in range(1, 6):
        subject.record(1, 4, message_id, [7], message_id, 10, historical=True)
    assert subject.record(1, 4, 1, [7], 1, 10) == []
    assert subject.record(1, 4, 6, [7], 11, 11) == [7]
