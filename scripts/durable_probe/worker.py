"""Synthetic DBOS/Pydantic AI child process; no provider or source requests."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
from pathlib import Path

from dbos import DBOS, SetWorkflowID
from pydantic_ai import Agent, models
from pydantic_ai.durable_exec.dbos import DBOSDurability
from pydantic_ai.models.test import TestModel

ROOT = Path(sys.argv[1]).resolve()
MODE = sys.argv[2]
VERSION = sys.argv[3]
WORKFLOW_ID = "synthetic-b05-restart-run"
models.ALLOW_MODEL_REQUESTS = False
os.umask(0o077)


def deny_network(*_args, **_kwargs):
    raise RuntimeError("This synthetic probe forbids network access")


socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
socket.create_connection = deny_network


def event(kind: str, **values):
    payload = {"event": kind, "pid": os.getpid(), "at": time.time(), **values}
    with (ROOT / "events.jsonl").open("a") as stream:
        stream.write(json.dumps(payload, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(payload, sort_keys=True), flush=True)


class CountingTestModel(TestModel):
    async def request(self, messages, model_settings, model_request_parameters):
        prompts = [
            part.content
            for message in messages
            if message.kind == "request"
            for part in message.parts
            if part.part_kind == "user-prompt"
        ]
        event("synthetic_model_request", prompt=prompts[-1])
        return await super().request(messages, model_settings, model_request_parameters)


DBOS(
    config={
        "name": "bidops-b05-probe",
        "application_version": VERSION,
        "executor_id": "single-local-probe-worker",
        "system_database_url": f"sqlite:///{ROOT / 'dbos.sqlite'}",
        "enable_otlp": False,
        "log_level": "INFO",
    }
)

agent = Agent(
    CountingTestModel(custom_output_text="SYNTHETIC_NOT_A_REAL_SCREENING_RESULT"),
    name="bidops_probe_agent",
    capabilities=[DBOSDurability(parallel_execution_mode="sequential")],
)


@DBOS.step()
async def checkpoint_gate():
    # The controller also verifies the preceding model's actual DBOS checkpoint.
    event("checkpoint_gate_entered")
    while not (ROOT / "release-gate").exists():
        await asyncio.sleep(0.05)
    event("checkpoint_gate_released")
    return "released"


@DBOS.step()
def final_step(first: str, second: str):
    event("final_step_called")
    return {"first": first, "second": second, "fixture": True}


@DBOS.workflow()
async def screening_probe():
    first = await agent.run("SYNTHETIC_FIRST_MODEL_STEP")
    await checkpoint_gate()
    second = await agent.run("SYNTHETIC_SECOND_MODEL_STEP")
    return final_step(first.output, second.output)


def main():
    event("worker_starting", mode=MODE, application_version=VERSION)
    DBOS.launch()
    try:
        event("dbos_launched", mode=MODE)
        if MODE == "submit":
            with SetWorkflowID(WORKFLOW_ID):
                handle = DBOS.start_workflow(screening_probe)
            event("workflow_submitted", workflow_id=handle.workflow_id)
        elif MODE in {"recover", "read-completed"}:
            # Recovery belongs to DBOS.launch(), never a second workflow submission.
            handle = DBOS.retrieve_workflow(WORKFLOW_ID)
        else:
            raise ValueError("unknown probe mode")
        result = handle.get_result()
        steps = DBOS.list_workflow_steps(WORKFLOW_ID, load_output=False)
        (ROOT / f"steps-{MODE}.json").write_text(json.dumps(steps, default=str, indent=2))
        (ROOT / f"result-{MODE}.json").write_text(json.dumps(result, indent=2))
        event("workflow_result", mode=MODE, result=result)
    finally:
        DBOS.destroy()


if __name__ == "__main__":
    main()
