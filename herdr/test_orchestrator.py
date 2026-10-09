"""Supervisor state-machine and delivery tests; no live agents or model calls."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "supervisor", Path(__file__).parent / ".config/herdr/plugins/orchestrator/supervisor.py")
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


def agent(pane="w1:p2", status="working", session="worker"):
    return {"pane_id": pane, "agent": "opencode", "agent_status": status,
            "agent_session": {"value": session}}


def initial():
    return {"paused": False, "projects": {}, "events": [], "workers": {
        "w1:p2": {"kind": "opencode", "session": "worker", "project": "demo",
                  "task": "T01", "stall_minutes": 20, "observed": "working",
                  "since": 0, "settled": False}},
        "coordinator": {"pane": "w1:p1", "kind": "opencode", "session": "coord"}}


class SupervisorTests(unittest.TestCase):
    def test_dwell_dedup_and_second_turn(self):
        state = initial()
        def observe(status, now):
            supervisor.observe(state, {"w1:p2": agent(status=status)}, now)
        observe("idle", 10)
        observe("idle", 14)
        self.assertEqual(state["events"], [])
        observe("idle", 16)
        observe("done", 20)
        observe("done", 26)
        self.assertEqual(len(state["events"]), 1)
        observe("working", 30)
        observe("working", 36)
        observe("done", 40)
        observe("done", 46)
        self.assertEqual(len(state["events"]), 1)  # same submitted turn
        state["workers"]["w1:p2"].update(terminal_queued=False, reported=None, turn="next")
        observe("done", 52)  # a newly watched fast task need not show working
        self.assertEqual(len(state["events"]), 2)

    def test_replaced_session_is_never_adopted(self):
        state = initial()
        agents = {"w1:p2": agent(session="stranger")}
        supervisor.observe(state, agents, 10)
        supervisor.observe(state, agents, 16)
        self.assertEqual(state["events"][0]["status"], "missing-or-replaced")
        self.assertEqual(state["workers"]["w1:p2"]["session"], "worker")

    def test_finished_and_unowned_agents_do_not_wake(self):
        state = initial()
        state["workers"]["w1:p2"]["finished"] = True
        supervisor.observe(state, {"w2:p1": agent(status="blocked")}, 2000)
        self.assertEqual(state["events"], [])

    def test_stall_is_once_per_working_period(self):
        state = initial()
        agents = {"w1:p2": agent()}
        supervisor.observe(state, agents, 1201)
        supervisor.observe(state, agents, 2401)
        self.assertEqual(len(state["events"]), 1)
        self.assertEqual(state["events"][0]["status"], "check-progress")

    def test_delivery_only_when_coordinator_ready_and_never_replays_uncertain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = initial()
            supervisor.enqueue(state, "w1:p2", state["workers"]["w1:p2"], "done", 0)
            state["workers"]["w1:p2"]["finished"] = True
            with supervisor.transaction(root) as saved:
                saved.update(state)
            coord = agent("w1:p1", "working", "coord")
            def api(*args):
                if args == ("agent", "list"):
                    return {"agents": [coord]}
                raise RuntimeError("delivery may have occurred")
            with patch.object(supervisor, "api", side_effect=api) as mock:
                supervisor.tick(root)
                self.assertEqual(mock.call_count, 1)
                coord["agent_status"] = "idle"
                supervisor.tick(root)
                self.assertEqual(mock.call_count, 3)
                supervisor.tick(root)
                self.assertEqual(mock.call_count, 4)  # list only; no retry
            with supervisor.transaction(root) as saved:
                self.assertEqual(saved["events"][0]["delivery"], "uncertain")

    def test_pause_and_coordinator_identity_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = initial()
            supervisor.enqueue(state, "w1:p2", state["workers"]["w1:p2"], "done", 0)
            state["workers"]["w1:p2"]["finished"] = True
            state["paused"] = True
            with supervisor.transaction(root) as saved:
                saved.update(state)
            with patch.object(supervisor, "api", return_value={"agents": [agent("w1:p1", "idle", "coord")]}) as mock:
                supervisor.tick(root)
                self.assertEqual(mock.call_count, 1)
            with supervisor.transaction(root) as saved:
                saved["paused"] = False
            with patch.object(supervisor, "api", return_value={"agents": [agent("w1:p1", "idle", "other")]}) as mock:
                supervisor.tick(root)
                self.assertEqual(mock.call_count, 1)

    def test_successful_batch_waits_for_acknowledgement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = initial()
            supervisor.enqueue(state, "w1:p2", state["workers"]["w1:p2"], "done", 0)
            state["workers"]["w1:p2"]["finished"] = True
            with supervisor.transaction(root) as saved:
                saved.update(state)
            def api(*args):
                if args == ("agent", "list"):
                    return {"agents": [agent("w1:p1", "idle", "coord")]}
                return {"type": "agent_prompted"}
            with patch.object(supervisor, "api", side_effect=api) as mock:
                supervisor.tick(root)
                supervisor.tick(root)
                self.assertEqual(mock.call_count, 3)
            with supervisor.transaction(root) as saved:
                self.assertEqual(saved["events"][0]["delivery"], "sent")
                saved["workers"]["w1:p2"]["observed"] = "blocked"
                supervisor.enqueue(saved, "w1:p2", saved["workers"]["w1:p2"], "blocked", 1)
            with patch.object(supervisor, "api", side_effect=api) as mock:
                supervisor.tick(root)
                self.assertEqual(mock.call_count, 1)
            with supervisor.transaction(root) as saved:
                saved["events"][0]["delivery"] = "acknowledged"
                saved["workers"]["w1:p2"]["observed"] = "blocked"
            with patch.object(supervisor, "api", side_effect=api) as mock:
                supervisor.tick(root)
                self.assertEqual(mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
