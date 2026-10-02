# Repeated mention protection implementation plan

> Execute sequentially in the isolated mention-protection branch. Use test-driven development and review the changes before deployment.

Goal: warn on the fourth direct mention of the same human member in a rolling hour, throughout the server; preserve the three-mention native message limit.

Architecture: a bounded tracker counts unique messages per guild, sender, and target. CommunityBot invokes it before the staff exemption, sends a warning with controlled mentions, and logs the result privately. Discord history initializes counts and warning cooldowns after startup.

Tech stack: Python 3.11+, discord.py, pytest, existing MCP application; no new dependency.

Constraints: three qualifying messages allowed; 3,600-second rolling window; one warning per sender per hour; no bans/timeouts/role changes; do not ping the target; include staff; isolate guilds; preserve other passive checks.

## Task 1: tracker and event integration

- [ ] Write tests in tests/test_mentions.py that fail because MentionTracker does not exist. Use controlled timestamps and real tracker operations to verify the fourth message, expiration, duplicate IDs, cross-target and cross-guild isolation, historical initialization, and sender cooldown.
- [x] Implement MentionTracker in the existing focused src/discord_mcp/moderation.py with record(guild_id, author_id, message_id, target_ids, timestamp, now, historical=False), returning triggered target IDs. Retain only the latest four unique message IDs per sender/target and prune entries older than the rolling window. Exclude self-mentions. Do not commit cooldown until a public warning or private fallback log succeeds.
- [ ] Add defaults to config.py for repeated_mention_limit=3 and repeated_mention_window_seconds=3600, parsed with existing positive-number validation.
- [ ] Add CommunityBot integration tests for staff coverage, bot and self exclusions, permitted mentions, no warning for an empty/foreign-guild message, and failed reply handling.
- [ ] Implement CommunityBot._check_repeated_mentions before the ordinary staff exemption, collecting only human targets. Reply using AllowedMentions(everyone=False, roles=False, users=[message.author], replied_user=False) and mention_author=False. Record a RuleSignal("3", "Repeated member pings", reason) through post_incident, whose privacy checks stay intact.

Example tracker behavior:

```python
tracker = MentionTracker()
for message_id in range(1, 4):
    assert tracker.record(1, 4, message_id, [7], message_id, 10) == []
assert tracker.record(1, 4, 4, [7], 4, 10) == [7]
tracker.mark_warned(1, 4, 10)
assert tracker.record(1, 4, 5, [7], 5, 11) == []
```

Run: python -m pytest tests/test_mentions.py tests/test_community.py tests/test_config.py -q. Expected: initial failures for missing behavior, then all tests pass.

## Task 2: restart recovery and runtime verification

- [ ] Write recovery tests using controlled async channel histories. Confirm history is read once per new READY session, human mentions initialize counts without replaying warnings, pending live messages wait for recovery, and warning cooldowns recover from private staff-log incidents.
- [ ] Implement bounded history recovery for accessible text channels and active threads from the preceding hour. Ignore messages created after the recovery cutoff to avoid swallowing live messages. Mark recovery incomplete if access or API requests fail; log the limitation.
- [ ] Add health output indicating repeated mention limit, window, tracker readiness and history recovery completeness, to permit verification without testing on a real member.
- [ ] Document the rule and exclusions in README.md. Verify warnings cannot emit owner, target, role or everyone notifications.
- [ ] Run python -m pytest -q from the isolated project with its src directory on PYTHONPATH. Run git diff --check and review the diff.

## Task 3: deployment

- [ ] Verify GitHub main still points at the original base SHA 71e304e16cd21e474b1c335f5eb90224f56daba1; if it changed, rebase the edits onto the new source and rerun affected checks.
- [ ] Commit the tested source, tests and documentation atomically and update main without force. This repository feeds the existing Render deployment. Do not start another Discord gateway process.
- [ ] Confirm the live /health endpoint reports the new configuration and Discord ready. If auto-deploy does not run, inspect the existing Render service and trigger its deployment only after proving the source and service match. Do not claim hourly warnings are active before live verification succeeds.
