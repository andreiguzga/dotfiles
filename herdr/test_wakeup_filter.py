"""Deterministic wakeup regressions: mocked transport and temporary state only."""
from pathlib import Path
from contextlib import contextmanager
import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_attention_events import supervisor, attention, store, permission, question, quota, WORKER, SESSION, COORDINATOR


class WakeupTests(unittest.TestCase):
    def queue(self, state, actions, now=100):
        for item in actions:
            if item["action"] == "triage":
                if item["detail"].get("audit_event_id"):
                    continue
                supervisor.enqueue(state, item["pane"], state["workers"][item["pane"]], item["status"], now)
                state["events"][-1]["attention"] = item["detail"]

    def test_live_fixture_resolved_then_late_duplicate_never_relatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with supervisor.transaction(root) as state:
                state.update(store())
                for delivery in ("sent", "uncertain"):
                    supervisor.enqueue(state, WORKER, state["workers"][WORKER], "DECISION", 99)
                    state["events"][-1]["delivery"] = delivery
                receipts = [dict(e) for e in state["events"]]
            request_id = "per_120950a26001aYFFxA5gYkx0hs"
            asked = permission(request_id, title="git log -1 --oneline")
            actions = supervisor.record_events(root, [asked], 100)
            with patch.object(supervisor, "api", return_value={"shown": True}) as api, \
                    patch.object(supervisor.time, "time", return_value=100):
                supervisor.deliver(root, actions, 100)
                self.assertEqual(api.call_count, 1)
            resolved = dict(asked, kind="resolve", reply="once")
            actions = supervisor.record_events(root, [resolved], 100)
            supervisor.triage_events(root, actions, 100)
            self.assertEqual(actions[0]["detail"]["waited_seconds"], 0)
            # Reload from disk and replay well beyond the toast cooldown: it is
            # resolution, not notification dedup, that prevents the phantom.
            for now in (101, 221):
                actions = supervisor.record_events(root, [asked], now)
                self.assertEqual(actions, [])
                with patch.object(supervisor, "api") as api:
                    supervisor.deliver(root, actions, now)
                    self.assertEqual(api.call_count, 0)
                with supervisor.transaction(root) as state:
                    self.assertEqual(attention.state(state)["pending"], {})
                    self.assertEqual(supervisor.wakeups(state, now), [])
                    self.assertEqual(state["events"][:2], receipts)
            # Same-session rewatch/cache eviction must not resurrect audit-backed
            # resolution. A new request id remains fully actionable.
            with supervisor.transaction(root) as state:
                supervisor.retire_turn(state, WORKER, 222)
                state["workers"][WORKER]["turn"] = "next"
                attention.state(state)["resolved"].clear()
            self.assertEqual(supervisor.record_events(root, [asked], 223), [])
            other = permission("per_different", title="different native request")
            actions = supervisor.record_events(root, [other], 224)
            with patch.object(supervisor, "api", return_value={"shown": True}) as api, \
                    patch.object(supervisor.time, "time", return_value=224):
                supervisor.deliver(root, actions, 224)
                self.assertEqual(api.call_count, 1)
            with supervisor.transaction(root) as state:
                self.assertEqual(set(attention.state(state)["pending"][WORKER]), {"per_different"})
                ready = supervisor.wakeups(state, 345)
                self.assertEqual([e["attention"]["request_id"] for e in ready], ["per_different"])
                self.assertEqual(state["events"][:2], receipts)

    def test_early_resolution_cache_is_bounded_and_exact_session(self):
        state = store()
        asked = permission("per_early")
        self.assertEqual(attention.apply(state, dict(asked, kind="resolve"), 100), [])
        self.assertEqual(attention.apply(state, asked, 101), [])
        # A resolution for the old session cannot suppress another session.
        state["workers"][WORKER]["session"] = "new-session"
        actions = attention.apply(state, dict(asked, session="new-session"), 102)
        self.assertEqual(len([a for a in actions if a["action"] == "notify"]), 1)
        for index in range(attention.MAX_SEEN + 10):
            attention.apply(state, dict(asked, session="new-session", kind="resolve",
                                        request_id=f"per_{index}"), 200 + index)
        self.assertLessEqual(len(attention.state(state)["resolved"]), attention.MAX_SEEN)

    def test_resolved_before_delivery_zero_prompts_and_audit_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = store()
            self.queue(state, attention.apply(state, permission(), 100))
            self.queue(state, attention.apply(state, dict(permission(), kind="resolve"), 110), 110)
            with supervisor.transaction(root) as saved:
                saved.update(state)
            agents = [{"pane_id": pane, "agent": "opencode", "agent_status": status,
                       "agent_session": {"value": session}}
                      for pane, status, session in ((WORKER, "working", SESSION),
                                                   (COORDINATOR, "idle", "ses_coord"))]
            with patch.object(supervisor, "api", return_value={"agents": agents}) as api, \
                    patch.object(supervisor.time, "time", return_value=221):
                supervisor.tick(root)
                self.assertEqual(api.call_count, 1)
            with supervisor.transaction(root) as saved:
                self.assertEqual(len(saved["events"]), 2)
                self.assertTrue(all(e["delivery"] == "retired" for e in saved["events"]))

    def test_unresolved_grace_and_blocked_native_suppression(self):
        state = store()
        self.queue(state, attention.apply(state, permission(), 100))
        supervisor.enqueue(state, WORKER, state["workers"][WORKER], "blocked", 105)
        supervisor.enqueue(state, WORKER, state["workers"][WORKER], "check-progress", 105)
        self.assertEqual(supervisor.wakeups(state, 110), [])
        ready = supervisor.wakeups(state, 221)
        self.assertEqual([e["status"] for e in ready], ["attention-permission"])
        self.assertFalse(ready[0]["attention"]["answered_by_supervisor"])

    def test_transient_churn_and_stable_terminal_once(self):
        state = store()
        def observe(status, now):
            supervisor.observe(state, {WORKER: {"agent": "opencode", "agent_status": status,
                                                "agent_session": {"value": SESSION}}}, now)
        for status, now in (("idle", 1), ("working", 2), ("done", 3), ("working", 4)):
            observe(status, now)
        self.assertEqual(supervisor.wakeups(state, 5), [])
        observe("done", 10)
        observe("done", 16)
        observe("working", 20)
        observe("working", 26)
        observe("idle", 30)
        observe("idle", 36)
        self.assertEqual([e["status"] for e in supervisor.wakeups(state, 36)], ["done"])

    def test_quota_retry_countdown_coalesces_but_error_and_decision_survive(self):
        state = store()
        for attempt in range(2, 8):
            self.queue(state, attention.apply(state, quota(attempt=attempt, message=f"quota retry in {attempt}s"), 100 + attempt))
        # Equivalent pending retry facts from older producers also coalesce.
        supervisor.enqueue(state, WORKER, state["workers"][WORKER], "attention-quota", 110)
        state["events"][-1]["attention"] = {"message_key": "provider-limit"}
        self.queue(state, attention.apply(state, dict(quota(), kind="error", message="authentication failed"), 111))
        supervisor.enqueue(state, WORKER, state["workers"][WORKER], "DECISION", 112)
        ready = supervisor.wakeups(state, 120)
        self.assertEqual([e["status"] for e in ready], ["attention-quota", "attention-error", "DECISION"])
        self.assertEqual(state["events"][1]["delivery"], "coalesced")
        self.assertEqual(attention.state(state)["quota"][WORKER]["kind"], "error")

    def test_recovered_blocked_not_delivered_genuine_blocked_and_missing_survive(self):
        state = store()
        supervisor.enqueue(state, WORKER, state["workers"][WORKER], "blocked", 100)
        self.assertEqual(supervisor.wakeups(state, 110), [])
        state["workers"][WORKER]["observed"] = "blocked"
        supervisor.enqueue(state, WORKER, state["workers"][WORKER], "blocked", 120)
        supervisor.enqueue(state, WORKER, state["workers"][WORKER], "missing-or-replaced", 121)
        self.assertEqual(len(supervisor.wakeups(state, 130)), 2)

    def test_retry_recovery_before_delivery_preserves_errors_and_later_episodes(self):
        state = store()
        self.queue(state, attention.apply(state, quota(message="connection reset"), 100))
        self.queue(state, attention.apply(state, dict(quota(), kind="retry-recovered"), 100))
        self.assertEqual(supervisor.wakeups(state, 110), [])
        self.queue(state, attention.apply(state, quota(message="connection reset"), 100))
        self.assertEqual(len(supervisor.wakeups(state, 111)), 1)
        self.queue(state, attention.apply(state, dict(quota(), kind="error"), 112))
        self.assertEqual(attention.apply(state, dict(quota(), kind="retry-recovered"), 113), [])
        self.assertIn("attention-error", [e["status"] for e in supervisor.wakeups(state, 114)])

    def test_rewatch_retires_pending_not_sent_or_uncertain(self):
        state = store()
        worker = state["workers"][WORKER]
        worker["turn"] = "old"
        for delivery in ("pending", "sent", "uncertain"):
            supervisor.enqueue(state, WORKER, worker, "done", 100)
            state["events"][-1]["delivery"] = delivery
        supervisor.retire_turn(state, WORKER, 110)
        worker.update(turn="new", session="replacement", task="T05")
        self.assertEqual(supervisor.wakeups(state, 120), [])
        self.assertEqual([e["delivery"] for e in state["events"]], ["retired", "sent", "uncertain"])

    def test_same_label_cli_rewatch_and_fast_completion_exact_turn_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = store()
            state["projects"]["demo"] = {"cwd": tmp, "tools": ["opencode"]}
            old_actions = attention.apply(state, permission(), 100)
            self.queue(state, old_actions)
            with supervisor.transaction(root) as saved:
                saved.update(state)
            live = {"pane_id": WORKER, "agent": "opencode", "agent_status": "idle",
                    "agent_session": {"value": SESSION}}
            argv = ["supervisor.py", "watch", WORKER, "--project", "demo", "--task", "T04"]
            with patch.object(supervisor, "state_root", return_value=root), \
                    patch.object(supervisor, "api", return_value={"agents": [live]}), \
                    patch.object(sys, "argv", argv), patch("sys.stdout", new_callable=io.StringIO), \
                    patch.object(supervisor.time, "time", return_value=200):
                supervisor.main()
                with supervisor.transaction(root) as saved:
                    first_turn = saved["workers"][WORKER]["turn"]
                supervisor.main()
            with supervisor.transaction(root) as saved:
                self.assertNotEqual(first_turn, saved["workers"][WORKER]["turn"])
                self.assertEqual(saved["events"][0]["delivery"], "retired")
                supervisor.observe(saved, {WORKER: live}, 206)
                ready = supervisor.wakeups(saved, 206)
                self.assertEqual([e["status"] for e in ready], ["idle"])
            with patch.object(supervisor, "api") as api:
                supervisor.notify(old_actions, root)
                self.assertEqual(api.call_count, 0)
            supervisor.triage_events(root, old_actions, 207)
            with supervisor.transaction(root) as saved:
                self.assertEqual(saved["events"][-1]["delivery"], "retired")
                self.assertEqual(saved["events"][-1]["filter_reason"], "late-superseded-action")

    def test_replaced_session_cannot_remind_native_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = store()
            state["workers"][WORKER].update(observed="blocked", since=700)
            attention.apply(state, permission(), 100)
            with supervisor.transaction(root) as saved:
                saved.update(state)
            replacement = {"pane_id": WORKER, "agent": "opencode", "agent_status": "blocked",
                           "agent_session": {"value": "stranger"}}
            with patch.object(supervisor, "api", return_value={"agents": [replacement]}) as api, \
                    patch.object(supervisor.time, "time", return_value=701):
                supervisor.tick(root)
                self.assertEqual(api.call_count, 1)  # no reminder to the replaced pane

    def test_multi_project_isolation_for_same_task_and_request_ids(self):
        state = store()
        second = "w2:p2"
        state["workers"][second] = dict(state["workers"][WORKER], project="other", session="other")
        self.queue(state, attention.apply(state, question(), 100))
        self.queue(state, attention.apply(state, question(pane=second, session="other"), 100))
        self.queue(state, attention.apply(state, dict(question(), kind="resolve"), 110))
        ready = supervisor.wakeups(state, 221)
        self.assertEqual([(e["pane"], e["project"]) for e in ready], [(second, "other")])

    def test_busy_pause_sent_uncertain_and_short_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = store()
            state["workers"][WORKER]["finished"] = True
            supervisor.enqueue(state, WORKER, state["workers"][WORKER], "done", 100)
            state["events"][0]["attention"] = {"message": "large evidence " * 1000}
            coord_status = "working"
            calls = []
            def api(*args):
                calls.append(args)
                if args[:2] == ("agent", "list"):
                    return {"agents": [{"pane_id": COORDINATOR, "agent": "opencode",
                                        "agent_status": coord_status,
                                        "agent_session": {"value": "ses_coord"}}]}
                return {}
            with supervisor.transaction(root) as saved:
                saved.update(state)
            with patch.object(supervisor, "api", side_effect=api):
                supervisor.tick(root)  # busy
                coord_status = "idle"
                with supervisor.transaction(root) as saved:
                    saved["paused"] = True
                supervisor.tick(root)  # paused
                with supervisor.transaction(root) as saved:
                    saved["paused"] = False
                supervisor.tick(root)
                supervisor.tick(root)  # sent awaits ack
                with supervisor.transaction(root) as saved:
                    saved["events"][0]["delivery"] = "uncertain"
                supervisor.tick(root)
            prompts = [c for c in calls if c[:2] == ("agent", "prompt")]
            self.assertEqual(len(prompts), 1)
            self.assertLess(len(prompts[0][-1]), 1000)
            self.assertNotIn("large evidence", prompts[0][-1])


class ResolutionIngressDeliveryTests(unittest.TestCase):
    """Exercise the real event CLI, spool, application, notify and tick pipeline."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = 100
        self.worker_status = "working"
        self.coordinator_status = "idle"
        self.calls = []
        with supervisor.transaction(self.root) as state:
            state.update(store())
            state["workers"][WORKER].update(since=100, stall_minutes=100000)
        for mocked in (
                patch.object(supervisor, "state_root", return_value=self.root),
                patch.object(supervisor, "api", side_effect=self.api),
                patch.object(supervisor.time, "time", side_effect=lambda: self.now)):
            mocked.start()
            self.addCleanup(mocked.stop)

    def api(self, *args):
        self.calls.append(args)
        if args[:2] == ("agent", "list"):
            return {"agents": [
                {"pane_id": pane, "agent": "opencode", "agent_status": status,
                 "agent_session": {"value": session}}
                for pane, status, session in ((WORKER, self.worker_status, SESSION),
                                             (COORDINATOR, self.coordinator_status, "ses_coord"))]}
        if args[:2] == ("notification", "show"):
            return {"shown": True, "reason": "mocked"}
        if args[:2] == ("agent", "prompt"):
            return {"type": "agent_prompted"}
        raise AssertionError(f"unexpected transport call: {args}")

    def ingress(self, records):
        with patch.object(sys, "argv", ["supervisor.py", "event"]), \
                patch("sys.stdin", io.StringIO("".join(json.dumps(r) + "\n" for r in records))), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            supervisor.main()
            return json.loads(output.getvalue())

    def control(self, *args):
        with patch.object(sys, "argv", ["supervisor.py", *args]), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            supervisor.main()
            return json.loads(output.getvalue())

    def saved(self):
        with supervisor.transaction(self.root) as state:
            return state

    def transport_calls(self, kind):
        return [call for call in self.calls if call[:2] == kind]

    def assert_no_notice_or_prompt(self):
        self.assertEqual(self.transport_calls(("notification", "show")), [])
        self.assertEqual(self.transport_calls(("agent", "prompt")), [])

    def resolved_first(self):
        asked = permission("per_resolve_first", title="git log -1 --oneline")
        self.ingress([dict(asked, kind="resolve", reply="once")])
        audit = [e for e in self.saved()["events"] if e["status"] == "attention-resolved"]
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["attention"]["request_id"], asked["request_id"])
        self.assertTrue(audit[0]["attention"]["unmatched"])
        return asked

    def test_resolve_first_survives_cache_eviction_through_cli_and_tick(self):
        asked = self.resolved_first()
        for index in range(attention.MAX_SEEN):
            self.now += 1
            self.ingress([dict(permission(f"per_other_{index}"), kind="resolve")])
        state = self.saved()
        self.assertNotIn(attention.fingerprint(WORKER, SESSION, asked["request_id"]),
                         attention.state(state)["resolved"])
        self.assertEqual(len(state["events"]), attention.MAX_SEEN + 1)
        self.ingress([asked])
        self.now += supervisor.NATIVE_ESCALATION_GRACE + 1
        supervisor.tick(self.root)
        self.assertEqual(attention.state(self.saved())["pending"], {})
        self.assert_no_notice_or_prompt()
        # A different id still traverses real notice delivery and escalation.
        self.ingress([permission("per_still_actionable")])
        self.now += supervisor.NATIVE_ESCALATION_GRACE + 1
        supervisor.tick(self.root)
        self.assertEqual(len(self.transport_calls(("notification", "show"))), 1)
        self.assertEqual(len(self.transport_calls(("agent", "prompt"))), 1)

    def test_resolve_first_survives_cache_expiry_through_cli_and_tick(self):
        asked = self.resolved_first()
        self.now += attention.SEEN_TTL + 1
        self.ingress([dict(permission("per_later"), kind="resolve")])
        self.assertNotIn(attention.fingerprint(WORKER, SESSION, asked["request_id"]),
                         attention.state(self.saved())["resolved"])
        self.ingress([asked])
        self.now += supervisor.NATIVE_ESCALATION_GRACE + 1
        supervisor.tick(self.root)
        self.assertEqual(attention.state(self.saved())["pending"], {})
        self.assert_no_notice_or_prompt()

    def test_resolution_audit_commits_even_if_cli_delivery_fails(self):
        asked = permission("per_commit_before_delivery")
        with patch.object(supervisor, "notify", side_effect=RuntimeError("mock delivery interruption")):
            with self.assertRaises(RuntimeError):
                self.ingress([dict(asked, kind="resolve")])
        audit = self.saved()["events"]
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["attention"]["request_id"], asked["request_id"])
        self.now += attention.SEEN_TTL + 1
        self.ingress([dict(permission("per_prune"), kind="resolve"), asked])
        supervisor.tick(self.root)
        self.assertEqual(attention.state(self.saved())["pending"], {})
        self.assert_no_notice_or_prompt()

    def stale_requests(self, ids):
        self.coordinator_status = "working"
        self.ingress([permission(request_id) for request_id in ids])
        self.worker_status = "idle"
        self.now = 101
        supervisor.tick(self.root)
        self.now = 101 + attention.PENDING_STALE_AFTER + 1
        supervisor.tick(self.root)
        unanswered = [e for e in self.saved()["events"] if e["status"] == "attention-unanswered"]
        self.assertEqual({e["attention"]["request_id"] for e in unanswered}, set(ids))
        return unanswered

    def test_resolved_unanswered_is_retired_before_delivery_and_review_stays_once(self):
        self.stale_requests(["per_stale"])
        self.ingress([dict(permission("per_stale"), kind="resolve", reply="once")])
        self.worker_status = "working"
        self.coordinator_status = "idle"
        self.now += 1
        supervisor.tick(self.root)
        unanswered = next(e for e in self.saved()["events"] if e["status"] == "attention-unanswered")
        self.assertEqual(unanswered["delivery"], "retired")
        self.assertEqual(unanswered["filter_reason"], "request-resolved-before-delivery")
        self.assertEqual(self.transport_calls(("agent", "prompt")), [])
        self.worker_status = "done"
        self.now += 10
        supervisor.tick(self.root)
        self.now += 6
        supervisor.tick(self.root)
        prompts = self.transport_calls(("agent", "prompt"))
        self.assertEqual(len(prompts), 1)
        self.assertNotIn("attention-unanswered", prompts[0][-1])
        with supervisor.transaction(self.root) as state:
            for event in state["events"]:
                if event["delivery"] == "sent":
                    event["delivery"] = "acknowledged"
        for status in ("working", "idle", "done"):
            self.worker_status = status
            self.now += 10
            supervisor.tick(self.root)
            self.now += 6
            supervisor.tick(self.root)
        self.assertEqual(len(self.transport_calls(("agent", "prompt"))), 1)

    def test_genuine_unanswered_is_delivered_but_resolved_receipts_stay_untouched(self):
        unanswered = self.stale_requests(["per_resolved", "per_genuine"])
        original = next(e for e in unanswered if e["attention"]["request_id"] == "per_resolved")
        receipts = []
        with supervisor.transaction(self.root) as state:
            for delivery in ("sent", "uncertain"):
                receipt = dict(original, id=f"receipt_{delivery}", delivery=delivery)
                state["events"].append(receipt)
                receipts.append(dict(receipt))
        self.ingress([dict(permission("per_resolved"), kind="resolve")])
        self.worker_status = "working"
        self.coordinator_status = "idle"
        self.now += 1
        supervisor.tick(self.root)
        self.assertEqual(self.transport_calls(("agent", "prompt")), [])
        state = self.saved()
        for receipt in receipts:
            self.assertEqual(next(e for e in state["events"] if e["id"] == receipt["id"]), receipt)
        genuine = next(e for e in state["events"] if e["status"] == "attention-unanswered"
                       and e["attention"]["request_id"] == "per_genuine")
        self.assertEqual(genuine["delivery"], "pending")
        # Explicit test acknowledgement releases the receipt gate, not the filter.
        with supervisor.transaction(self.root) as state:
            for event in state["events"]:
                if event["id"].startswith("receipt_"):
                    event["delivery"] = "acknowledged"
        supervisor.tick(self.root)
        self.assertEqual(len(self.transport_calls(("agent", "prompt"))), 1)
        state = self.saved()
        self.assertEqual(next(e for e in state["events"] if e["id"] == genuine["id"])["delivery"], "sent")
        self.assertEqual(next(e for e in state["events"] if e["id"] == original["id"])["delivery"], "retired")

    def test_same_batch_asked_resolved_and_late_asked_has_no_notice_or_prompt(self):
        for make in (permission, question):
            with self.subTest(kind=make.__name__):
                asked = make(f"request_same_batch_{make.__name__}")
                report = self.ingress([asked, dict(asked, kind="resolve"), asked])
                self.assertEqual(report["notices"], [])
                self.now += supervisor.NATIVE_ESCALATION_GRACE + 1
                supervisor.tick(self.root)
                self.assertEqual(attention.state(self.saved())["pending"], {})
                self.assert_no_notice_or_prompt()

    def test_interleaved_resolution_between_ingress_and_notice_dispatch(self):
        asked = permission("per_interleaved")
        notify = supervisor.notify
        interleaved = False
        def resolve_before_dispatch(actions, root):
            nonlocal interleaved
            if not interleaved and any(a.get("request_id") == asked["request_id"] for a in actions):
                interleaved = True
                self.ingress([dict(asked, kind="resolve", reply="once")])
            return notify(actions, root)
        with patch.object(supervisor, "notify", side_effect=resolve_before_dispatch):
            report = self.ingress([asked])
        self.assertTrue(interleaved)
        self.assertEqual(report["notices"], [])
        self.now += supervisor.NATIVE_ESCALATION_GRACE + 1
        supervisor.tick(self.root)
        self.assertEqual(attention.state(self.saved())["pending"], {})
        self.assert_no_notice_or_prompt()

    def test_resolution_after_notice_retry_intent_is_rechecked_before_dispatch(self):
        asked = permission("per_after_notice_intent")
        transaction = supervisor.transaction
        interleaved = False
        @contextmanager
        def resolve_after_intent(root):
            nonlocal interleaved
            with transaction(root) as state:
                yield state
                key = attention.fingerprint("permission", WORKER, SESSION, asked["request_id"])
                has_intent = key in attention.state(state)["attempts"]
            # The real preflight transaction has committed and released its lock.
            if has_intent and not interleaved:
                interleaved = True
                self.ingress([dict(asked, kind="resolve", reply="once")])
        with patch.object(supervisor, "transaction", side_effect=resolve_after_intent):
            report = self.ingress([asked])
        self.assertTrue(interleaved)
        self.assertEqual(report["notices"], [])
        self.now += supervisor.NATIVE_ESCALATION_GRACE + 1
        supervisor.tick(self.root)
        self.assert_no_notice_or_prompt()
        self.assertEqual(attention.state(self.saved())["attempts"], {})

    def test_provider_limit_idle_is_not_recovery_and_still_escalates(self):
        self.coordinator_status = "working"
        report = self.ingress([quota(attempt=5, message="monthly usage limit reached")])
        self.assertTrue(report["notices"][0]["shown"])
        supervisor.tick(self.root)
        self.worker_status = "idle"
        self.now += 1
        self.ingress([dict(quota(), kind="retry-recovered")])
        self.coordinator_status = "idle"
        self.now += 1
        supervisor.tick(self.root)
        state = self.saved()
        entry = attention.state(state)["quota"][WORKER]
        event = next(e for e in state["events"] if e["status"] == "attention-quota")
        self.assertEqual(entry["message_key"], "provider-limit")
        self.assertEqual(event["attention"]["message_key"], "provider-limit")
        self.assertEqual(event["delivery"], "sent")
        self.assertNotIn("filter_reason", event)
        self.assertEqual(len(self.transport_calls(("notification", "show"))), 1)
        self.assertEqual(len(self.transport_calls(("agent", "prompt"))), 1)
        self.assertIn("attention-quota", self.transport_calls(("agent", "prompt"))[0][-1])
        # Neither an ordinary idle dwell nor periodic alert aging proves that
        # provider credits/usage became available again. Sent evidence is intact.
        self.now += attention.QUOTA_STALE_AFTER + 1
        self.ingress([dict(quota(), kind="retry-recovered")])
        supervisor.tick(self.root)
        state = self.saved()
        self.assertEqual(attention.state(state)["quota"][WORKER]["message_key"], "provider-limit")
        self.assertEqual(next(e for e in state["events"] if e["id"] == event["id"]), event)
        self.assertEqual(len(self.transport_calls(("agent", "prompt"))), 1)

    def test_plain_retry_recovery_still_clears_and_retires(self):
        self.coordinator_status = "working"
        report = self.ingress([quota(attempt=5, message="connection reset; retrying")])
        self.assertTrue(report["notices"][0]["shown"])
        supervisor.tick(self.root)
        self.worker_status = "idle"
        self.now += 1
        self.ingress([dict(quota(), kind="retry-recovered")])
        self.coordinator_status = "idle"
        self.now += 1
        supervisor.tick(self.root)
        state = self.saved()
        self.assertEqual(attention.state(state)["quota"], {})
        event = next(e for e in state["events"] if e["status"] == "attention-quota")
        self.assertEqual(event["attention"]["message_key"], "retry")
        self.assertEqual(event["delivery"], "retired")
        self.assertEqual(event["filter_reason"], "retry-recovered-before-delivery")
        self.assertEqual(len(self.transport_calls(("notification", "show"))), 1)
        self.assertEqual(self.transport_calls(("agent", "prompt")), [])

    def test_same_batch_plain_retry_recovery_suppresses_only_stale_notice(self):
        report = self.ingress([quota(attempt=5, message="connection reset"),
                               dict(quota(), kind="retry-recovered")])
        self.assertEqual(report["notices"], [])
        supervisor.tick(self.root)
        self.assertEqual(attention.state(self.saved())["quota"], {})
        self.assert_no_notice_or_prompt()
        # An unresolved retry in a subsequent episode still notices/escalates.
        self.now += 1
        report = self.ingress([quota(attempt=5, message="connection reset")])
        self.assertTrue(report["notices"][0]["shown"])
        supervisor.tick(self.root)
        self.assertEqual(len(self.transport_calls(("notification", "show"))), 1)
        self.assertEqual(len(self.transport_calls(("agent", "prompt"))), 1)

    def test_active_limit_and_error_notices_survive_same_batch_retry_recovery(self):
        self.coordinator_status = "working"
        for record in (quota(attempt=5, message="monthly usage limit reached"),
                       dict(quota(), kind="error", message="authentication failed")):
            report = self.ingress([record, dict(quota(), kind="retry-recovered")])
            self.assertEqual(len(report["notices"]), 1)
            self.assertTrue(report["notices"][0]["shown"])
            self.now += 1
        self.coordinator_status = "idle"
        supervisor.tick(self.root)
        sent = [e["status"] for e in self.saved()["events"] if e["delivery"] == "sent"]
        self.assertEqual(sent, ["attention-quota", "attention-error"])
        self.assertEqual(len(self.transport_calls(("notification", "show"))), 2)
        self.assertEqual(len(self.transport_calls(("agent", "prompt"))), 1)

    def test_finished_worker_resolution_remains_durable_across_same_session_rewatch(self):
        asked = permission("per_finished")
        self.ingress([asked])
        with supervisor.transaction(self.root) as state:
            state["projects"]["demo"] = {"cwd": str(self.root), "tools": ["opencode"]}
            for delivery in ("sent", "uncertain"):
                supervisor.enqueue(state, WORKER, state["workers"][WORKER], "DECISION", self.now)
                state["events"][-1]["delivery"] = delivery
            receipts = [dict(e) for e in state["events"] if e["delivery"] in {"sent", "uncertain"}]
        self.control("finish", WORKER)
        self.ingress([dict(asked, kind="resolve", reply="once"), asked,
                      dict(asked, kind="resolve", session="stranger"),
                      dict(asked, kind="resolve", agent="claude")])
        state = self.saved()
        audit = [e for e in state["events"] if e["status"] == "attention-resolved"]
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["attention"]["request_id"], asked["request_id"])
        self.assertEqual(audit[0]["session"], SESSION)
        self.assertIn(attention.fingerprint(WORKER, SESSION, asked["request_id"]),
                      attention.state(state)["resolved"])
        self.control("watch", WORKER, "--project", "demo", "--task", "T04", "--stall-minutes", "100000")
        # Expire the accelerator through real ingress: the finished-time audit
        # must remain authoritative in the exact same session after rewatch.
        self.now += attention.SEEN_TTL + 1
        self.ingress([dict(permission("per_prune_finished"), kind="resolve"), asked])
        supervisor.tick(self.root)
        state = self.saved()
        self.assertEqual(attention.state(state)["pending"], {})
        self.assertEqual(len(self.transport_calls(("notification", "show"))), 1)
        self.assertEqual(self.transport_calls(("agent", "prompt")), [])
        for receipt in receipts:
            self.assertEqual(next(e for e in state["events"] if e["id"] == receipt["id"]), receipt)
        self.control("ack", *(e["id"] for e in receipts))
        self.ingress([permission("per_new_after_finish")])
        self.now += supervisor.NATIVE_ESCALATION_GRACE + 1
        supervisor.tick(self.root)
        self.assertEqual(len(self.transport_calls(("notification", "show"))), 2)
        self.assertEqual(len(self.transport_calls(("agent", "prompt"))), 1)

    def test_cli_rewatch_retires_old_lifecycle_only_without_losing_receipts_or_other_panes(self):
        self.coordinator_status = "working"
        self.worker_status = "idle"
        supervisor.tick(self.root)
        self.now += 6
        supervisor.tick(self.root)
        old_review = next(e for e in self.saved()["events"] if e["status"] == "idle")
        self.assertEqual(old_review["delivery"], "pending")
        with supervisor.transaction(self.root) as state:
            state["projects"]["demo"] = {"cwd": str(self.root), "tools": ["opencode"]}
            other = dict(old_review, id="other_project_pending", pane="w2:p2",
                         project="other-project", task="T99", session="other-session")
            state["events"].append(other)
            receipts = [dict(old_review, id=f"review_{delivery}", delivery=delivery)
                        for delivery in ("sent", "uncertain")]
            state["events"].extend(receipts)
        # Same label/session, but a new submitted turn. Previously queued idle
        # cannot serve as review evidence for the new fast task.
        watched = self.control("watch", WORKER, "--project", "demo", "--task", "T04")
        retired = next(e for e in watched["events"] if e["id"] == old_review["id"])
        self.assertEqual(retired["delivery"], "retired")
        self.assertEqual(retired["filter_reason"], "retired-watch-turn")
        self.assertEqual(next(e for e in watched["events"] if e["id"] == other["id"]), other)
        for receipt in receipts:
            self.assertEqual(next(e for e in watched["events"] if e["id"] == receipt["id"]), receipt)
        self.control("ack", other["id"], *(e["id"] for e in receipts))
        self.coordinator_status = "idle"
        self.now += 6
        supervisor.tick(self.root)
        state = self.saved()
        new_review = [e for e in state["events"] if e["status"] in {"idle", "done"}
                      and e.get("turn") == watched["workers"][WORKER]["turn"]]
        self.assertEqual(len(new_review), 1)
        self.assertEqual(new_review[0]["delivery"], "sent")
        self.assertEqual(len(self.transport_calls(("agent", "prompt"))), 1)
        self.assertNotIn(old_review["id"], self.transport_calls(("agent", "prompt"))[0][-1])
        self.control("ack", new_review[0]["id"])
        for status in ("working", "idle", "done"):
            self.worker_status = status
            self.now += 6
            supervisor.tick(self.root)
            self.now += 6
            supervisor.tick(self.root)
        self.assertEqual(len(self.transport_calls(("agent", "prompt"))), 1)
