Title: Durable Execution with DBOS

URL Source: https://ai.pydantic.dev/durable_execution/dbos/

Markdown Content:
[DBOS](https://www.dbos.dev/) is a lightweight [durable execution](https://docs.dbos.dev/architecture) library natively integrated with Pydantic AI.

DBOS workflows make your program **durable** by checkpointing its state in a database. If your program ever fails, when it restarts all your workflows will automatically resume from the last completed step.

*   **Workflows** must be deterministic and generally cannot include I/O.
*   **Steps** may perform I/O (network, disk, API calls). If a step fails, it restarts from the beginning.

Every workflow input and step output is durably stored in the system database. When workflow execution fails, whether from crashes, network issues, or server restarts, DBOS leverages these checkpoints to recover workflows from their last completed step.

DBOS **queues** provide durable, database-backed alternatives to systems like Celery or BullMQ, supporting features such as concurrency limits, rate limits, timeouts, and prioritization. See the [DBOS docs](https://docs.dbos.dev/architecture) for details.

The diagram below shows the overall architecture of an agentic application in DBOS. DBOS runs fully in-process as a library. Functions remain normal Python functions but are checkpointed into a database (Postgres or SQLite).

`Clients            (HTTP, RPC, Kafka, etc.)                        |                        v+------------------------------------------------------+|               Application Servers                    ||                                                      ||   +----------------------------------------------+   ||   |        Pydantic AI + DBOS Libraries          |   ||   |                                              |   ||   |  [ Workflows (Agent Run Loop) ]              |   ||   |  [ Steps (Tool, MCP, Model) ]                |   ||   |  [ Queues ]   [ Cron Jobs ]   [ Messaging ]  |   ||   +----------------------------------------------+   ||                                                      |+------------------------------------------------------+                        |                        v+------------------------------------------------------+|                      Database                        ||   (Stores workflow and step state, schedules tasks)  |+------------------------------------------------------+`
See the [DBOS documentation](https://docs.dbos.dev/architecture) for more information.

Add durable execution to any [`Agent`](https://pydantic.dev/docs/ai/api/pydantic-ai/agent/#pydantic_ai.agent.Agent) by attaching the [`DBOSDurability`](https://pydantic.dev/docs/ai/api/pydantic-ai/durable_exec/#pydantic_ai.durable_exec.dbos.DBOSDurability)[capability](https://pydantic.dev/docs/ai/capabilities/overview/). When the agent runs inside a DBOS workflow, the capability routes [model requests](https://pydantic.dev/docs/ai/models/overview/) and [MCP communication](https://pydantic.dev/docs/ai/mcp/client/) through DBOS steps. To make a run durable, call `agent.run()` inside a `@DBOS.workflow`.

The agent stays a normal `Agent` everywhere — outside a DBOS workflow the capability is transparent, and the original agent, model, and MCP server can still be used as normal.

Custom tool functions and event stream handlers registered on the agent directly or through another capability are **not automatically wrapped** by DBOS. An `event_stream_handler=` passed to `DBOSDurability` runs inside a DBOS step and receives live-streamed events. If they involve non-deterministic behavior or perform I/O, you should explicitly decorate them with `@DBOS.step`.

Here is a simple but complete example of attaching durable execution to an agent. All it requires is to install Pydantic AI with the DBOS [open-source library](https://github.com/dbos-inc/dbos-transact-py):

*   [pip](https://ai.pydantic.dev/durable_execution/dbos/#tab-panel-0)
*   [uv](https://ai.pydantic.dev/durable_execution/dbos/#tab-panel-1)

Terminal

`pip install pydantic-ai[dbos]`

Or if you’re using the slim package, you can install it with the `dbos` optional group:

*   [pip](https://ai.pydantic.dev/durable_execution/dbos/#tab-panel-2)
*   [uv](https://ai.pydantic.dev/durable_execution/dbos/#tab-panel-3)

Terminal

`pip install pydantic-ai-slim[dbos]`

After that, run the following example code:

dbos_durability.py

`from dbos import DBOS, DBOSConfigfrom pydantic_ai import Agentfrom pydantic_ai.durable_exec.dbos import DBOSDurabilitydbos_config: DBOSConfig = {    'name': 'pydantic_dbos_agent',    'system_database_url': 'sqlite:///dbostest.sqlite',  }DBOS(config=dbos_config)agent = Agent(    'openai:gpt-6.1-sol',    instructions="You're an expert in geography.",    name='geography',      capabilities=[DBOSDurability()],  )@DBOS.workflow()  async def answer(question: str) -> str:    result = await agent.run(question)    return result.outputasync def main():    DBOS.launch()    answer_text = await answer('What is the capital of Mexico?')    print(answer_text)    #> Mexico City (Ciudad de México, CDMX)`

This example uses SQLite. Postgres is recommended for production.

The agent's `name` is used to uniquely identify its workflows.

Attach durability via `capabilities=[...]`. The capability routes model requests and MCP communication through DBOS steps when the agent runs inside a workflow. Because DBOS workflows must be registered before `DBOS.launch()`, the agent must also be constructed before calling `DBOS.launch()`.

Wrap `agent.run()` in your own `@DBOS.workflow` to make the run durable.

_(To run this example, ensure `asyncio` is imported and add `asyncio.run(main())`; no other changes are needed.)_

Because the same agent works inside and outside a DBOS workflow, [`DBOSDurability`](https://pydantic.dev/docs/ai/api/pydantic-ai/durable_exec/#pydantic_ai.durable_exec.dbos.DBOSDurability) composes with all other [capabilities](https://pydantic.dev/docs/ai/capabilities/overview/) without each needing a DBOS-specific wrapper variant.

For more information on how to use DBOS in Python applications, see their [Python SDK guide](https://docs.dbos.dev/python/programming-guide).

Any agent can be wrapped in a [`DBOSAgent`](https://pydantic.dev/docs/ai/api/pydantic-ai/durable_exec/#pydantic_ai.durable_exec.dbos.DBOSAgent) to get a durable agent variant that routes model requests and MCP communication through DBOS steps:

dbos_agent.py

`from pydantic_ai import Agentfrom pydantic_ai.durable_exec.dbos import DBOSAgentagent = Agent('openai:gpt-6.1-sol', name='geography')dbos_agent = DBOSAgent(agent)  # Use `dbos_agent` in place of `agent`.`
Migrating to the capability means attaching `DBOSDurability` and adding the workflow decorator that `DBOSAgent` used to apply for you:

`-dbos_agent = DBOSAgent(agent)-result = await dbos_agent.run(prompt)+agent = Agent(..., capabilities=[DBOSDurability()])++@DBOS.workflow()+async def answer(prompt: str) -> str:+    result = await agent.run(prompt)+    return result.output`
When using DBOS with Pydantic AI agents, there are a few important considerations to ensure workflows and toolsets behave correctly.

Each agent instance must have a unique `name` so DBOS can correctly resume workflows after a failure or restart.

Each [`MCPToolset`](https://pydantic.dev/docs/ai/api/pydantic-ai/mcp/#pydantic_ai.mcp.MCPToolset) must have a unique [`id`](https://pydantic.dev/docs/ai/api/pydantic-ai/toolsets/#pydantic_ai.toolsets.AbstractToolset.id), as DBOS derives its step names and per-run tool-defs cache key from it. This field is normally optional, but is required when using DBOS. It should not be changed once the durable agent has been deployed to production, as this would break active workflows.

MCP tools perform I/O and always run in their DBOS step. Setting a tool’s `metadata={'dbos': False}` is rejected instead of running the MCP call inline in workflow code.

An MCP server is connected once per workflow rather than once per step: the first step that needs it connects it — inside a step, so DBOS retries a failed connection like any other step failure — and the workflow holds the session until the run ends. Tool discovery still runs its `get_tools` step, and whether it runs is decided by the run’s own recorded history rather than by what a warm process happens to hold, so the step sequence stays the same on recovery; the server’s own [`cache_tools`](https://pydantic.dev/docs/ai/api/pydantic-ai/mcp/#pydantic_ai.mcp.MCPToolset.cache_tools) then answers those steps from the session the run is already holding, without another round trip. Concurrent runs in one process share a server’s session, as they do outside a workflow, and it is closed once the last of them ends.

[`DynamicToolset`](https://pydantic.dev/docs/ai/api/pydantic-ai/toolsets/#pydantic_ai.toolsets.DynamicToolset)s, including those contributed by a [`DynamicCapability`](https://pydantic.dev/docs/ai/api/pydantic-ai/capabilities/#pydantic_ai.capabilities.DynamicCapability), are wrapped too and require a stable `id`. Tool discovery, argument validation, and calls run as `{name}__dynamic_toolset__{id}.get_tools`, `{name}__dynamic_toolset__{id}.validate_args`, and `{name}__dynamic_toolset__{id}.call_tool` steps. A tool’s [`args_validator`](https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/#args-validator) runs in the `validate_args` step, which re-resolves the toolset like `call_tool`; a tool without one schedules no validation step. Validation runs before [approval and deferral](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/), so rejected arguments never reach an approver. All of the dynamic toolset’s I/O — including MCP communication — happens inside those steps, so it is checkpointed. When the factory is built with `per_run_step=False`, the run resolves the toolset once and each step reuses it, entering it the first time a step needs it and leaving it entered for the rest of the run. Building a toolset is not the same as connecting it — an `MCPToolset` opens nothing until it is entered — so connecting happens inside a step, where it is retried like any other step failure. The factory itself runs in workflow code and re-runs whenever that replays, so like the capability factory below it must be deterministic given the run’s `deps`: build the toolset in the factory and leave its I/O to the steps. This is the lifecycle a non-durable run gives the toolset, so a toolset’s own caching — such as [`cache_tools`](https://pydantic.dev/docs/ai/api/pydantic-ai/mcp/#pydantic_ai.mcp.MCPToolset.cache_tools) on an `MCPToolset` the factory returns — works as it does outside a workflow, instead of being discarded between steps. A `per_run_step=True` factory is re-resolved inside each step, as it asks to be. For a `DynamicCapability`, DBOS reuses the capability resolved for the run inside those steps. The capability factory itself runs in workflow code and re-runs when a workflow recovers, so like all workflow code it must be deterministic given the run’s `deps`: construct the toolset in the factory and leave its I/O to the steps.

A toolset contributed by a [capability](https://pydantic.dev/docs/ai/capabilities/overview/) — a [`Capability`](https://pydantic.dev/docs/ai/api/pydantic-ai/capabilities/#pydantic_ai.capabilities.Capability) with `tools=`, or a locally-running [`MCP`](https://pydantic.dev/docs/ai/api/pydantic-ai/capabilities/#pydantic_ai.capabilities.MCP) server — derives its `id` from the capability’s own [`id`](https://pydantic.dev/docs/ai/api/pydantic-ai/capabilities/#pydantic_ai.capabilities.AbstractCapability.id), so set `Capability(id='...', tools=[...])` or `MCP(id='...', url='...')`. An `MCP` resolves its `id` in precedence order: an explicit `id=`, then a `native=MCPServerTool(...)` id, then a slug derived from the server URL’s host and path. A bare non-URL local client (e.g. `MCP(local=Path(...))`) with none of these stays id-less and must be given an explicit `id` to be used here.

Function tools and event stream handlers registered on the agent directly or through another capability are not automatically wrapped by DBOS. An `event_stream_handler=` passed to `DBOSDurability` runs inside a DBOS step and receives live-streamed events. For directly registered tools and handlers, you can decide how to integrate them:

*   Decorate with `@DBOS.step` if the function involves non-determinism or I/O.
*   Skip the decorator if durability isn’t needed, so you avoid the extra DB checkpoint write.
*   If the function needs to enqueue tasks or invoke other DBOS workflows, run it inside the agent’s main workflow (not as a step).

All other agents and toolsets are supported.

By default, DBOS checkpoints workflow inputs/outputs and step outputs into a database using [`pickle`](https://docs.python.org/3/library/pickle.html). But you can optionally supply a [custom serializer](https://docs.dbos.dev/python/reference/contexts#custom-serialization) through DBOS configuration. This means you need to make sure the [dependencies](https://pydantic.dev/docs/ai/core-concepts/dependencies/) object provided to `Agent.run()` / `Agent.run_sync()`, and tool outputs can be serialized. You may also want to keep the inputs and outputs small (under ~2 MB). PostgreSQL and SQLite support up to 1 GB per field, but large objects may impact performance.

If you don’t pass `run_id` or `conversation_id` to `Agent.run()` in a workflow, the defaults are derived from the workflow ID rather than generated at random, so a recovered workflow keeps the IDs its completed steps already saw.

Attach the [workspace](https://pydantic.dev/docs/ai/core-concepts/workspace/) capability, such as `LocalWorkspace`, when you construct the agent, and use `ctx.workspace` as in any run. Each workspace call made in workflow code, including from function tools, which DBOS runs in the workflow, is a step, so file contents and command output count toward the [size guidance above](https://ai.pydantic.dev/durable_execution/dbos/#agent-run-context-and-dependencies).

DBOS stores workflow-side workspace call arguments (commands, `env=`, file contents) in history. Keep secrets in the workspace capability’s `env=` instead of passing them to workspace calls; calls from function tools run in the workflow too, so their arguments are stored as well. Protect stored history with an appropriate payload codec.

Adding a workspace to an agent changes its workflows’ steps, so let in-flight workflows finish or deploy the change as a new application version; DBOS can’t recover a workflow started before it.

`Agent.run(model=...)` supports both model strings (like `'openai:gpt-6.1-sol'`) and model instances. A model instance can’t be serialized across the step boundary, and rebuilding one from its `model_id` string would build a _different_ model — the same model name on whatever provider the worker’s environment implies, so the request would go to another endpoint with other credentials. An instance that isn’t registered ahead of time is therefore rejected with a `UserError`. There are two ways to use a specific instance: pre-register it by passing a `models` dict to [`DBOSDurability`](https://pydantic.dev/docs/ai/api/pydantic-ai/durable_exec/#pydantic_ai.durable_exec.dbos.DBOSDurability) and reference it by key (or pass the registered instance), or pass a model-name string and build the instance inside the step with a [`ResolveModelId`](https://pydantic.dev/docs/ai/capabilities/resolve-model-id/) capability — the right choice when the model depends on the run’s `deps`, e.g. per-user credentials. Model-name strings themselves never need registering. The agent’s own model, set at construction, is always available as the default.

To customize how a model string is built — a custom provider, or per-user credentials carried on the run’s `deps` — add a [`ResolveModelId`](https://pydantic.dev/docs/ai/capabilities/resolve-model-id/) capability before `DBOSDurability`: it gets first crack at every string, and the resolver runs again inside the step with the run’s actual `deps`, so it must be deterministic for a given `(model_id, deps)` and must not perform external I/O.

Attach [capabilities](https://pydantic.dev/docs/ai/capabilities/overview/) when the agent is constructed, so `DBOSDurability.for_agent()` can register their steps before the application starts. Passing `agent.run(capabilities=[...])` inside a workflow raises a `UserError`: a capability added that late has no registered steps for the toolsets it contributes or for its own `@durable_operation` methods.

Capabilities that only observe the run are safe to attach per-run: their hooks read run state but don’t contribute tools, toolsets, or durable operations. [`Instrumentation`](https://pydantic.dev/docs/ai/api/pydantic-ai/capabilities/#pydantic_ai.capabilities.Instrumentation) is the built-in example and is exempt from the restriction. The current restriction is more conservative because third-party capabilities can’t yet declare that they only observe the run. Deriving this from the hooks a capability overrides is tracked in [#5477](https://github.com/pydantic/pydantic-ai/issues/5477); if you need a per-run capability inside a workflow, please share your use case there. Outside a workflow the durability capability is transparent, so per-run capabilities are fine there.

`Agent.run_stream()` and `Agent.run_stream_events()` work inside a DBOS workflow, but their events are buffered rather than delivered in real time. The model stream runs inside the durable step, and its events are replayed to the workflow after the step completes.

For handlers with I/O side effects, pass `event_stream_handler=` to [`DBOSDurability`](https://pydantic.dev/docs/ai/api/pydantic-ai/durable_exec/#pydantic_ai.durable_exec.dbos.DBOSDurability). Model events are delivered live inside each model-request step, while each tool event is delivered in its own event-handler step. As with any DBOS step, a handler may run more than once if the workflow recovers before its step is checkpointed, so keep its side effects idempotent.

Alternatively, register [`ProcessEventStream`](https://pydantic.dev/docs/ai/api/pydantic-ai/capabilities/#pydantic_ai.capabilities.ProcessEventStream). Its handler runs in workflow code and must be deterministic because it re-runs on workflow replay. Tool and final-output events arrive live, while the real captured model events are replayed after each model request completes. For examples, see the [streaming docs](https://pydantic.dev/docs/ai/core-concepts/agent/#streaming-all-events).

A durability `event_stream_handler=` and a separately registered `ProcessEventStream` are two distinct handlers, and each fires once. The durability handler receives live events inside the durable step, while `ProcessEventStream` sees the buffered replay in workflow code.

A per-run handler passed to `Agent.run(event_stream_handler=...)` also runs workflow-side against replayed model events.

Events emitted with [`ctx.emit()`](https://pydantic.dev/docs/ai/api/pydantic-ai/tools/#pydantic_ai.tools.RunContext.emit) from inside a DBOS step — an MCP or dynamic-toolset tool, an `event_stream_handler`, or a tool you decorated with `@DBOS.step` — are delivered when the step actually runs, and are _not_ re-emitted when its recorded result is replayed on recovery. Like a log line written inside a step, an emitted event is a side effect of running the step rather than part of its recorded result. Plain function tools and [capability](https://pydantic.dev/docs/ai/capabilities/overview/) hooks run in workflow code, where an emit re-runs with the rest of the workflow. Capability listeners registered with [`@on_event`](https://pydantic.dev/docs/ai/api/pydantic-ai/capabilities/#pydantic_ai.capabilities.on_event) run in workflow code too, so they re-run on recovery and must be deterministic; keep I/O in a durability `event_stream_handler=`, which runs in its own step. Unlike [`ctx.enqueue()`](https://pydantic.dev/docs/ai/api/pydantic-ai/tools/#pydantic_ai.tools.RunContext.enqueue), which is rejected inside a step because dropping it would change what the model sees, a missed event only means an observer wasn’t notified. Carrying a durable unit’s emitted events in its recorded output, so a replay reproduces them, is tracked in [pydantic-ai#7971](https://github.com/pydantic/pydantic-ai/issues/7971).

Because the model stream is consumed inside the step, cancelling it from the workflow side (e.g. with [`AgentStream.cancel()`](https://pydantic.dev/docs/ai/api/pydantic-ai/result/#pydantic_ai.result.AgentStream.cancel)) is not available across the durable boundary.

[`CancellationToken`](https://pydantic.dev/docs/ai/api/pydantic-ai/agent/#pydantic_ai.CancellationToken) cannot be passed to a DBOS durable run, and [`RunContext.cancel()`](https://pydantic.dev/docs/ai/api/pydantic-ai/tools/#pydantic_ai.tools.RunContext.cancel) raises a clear [`UserError`](https://pydantic.dev/docs/ai/api/pydantic-ai/exceptions/#pydantic_ai.exceptions.UserError) inside a step-wrapped unit (a dynamic or MCP tool, or an `event_stream_handler`) whose recorded result would replay without re-running on recovery. A plain function tool runs at workflow level under DBOS, where `cancel()` works and is replay-consistent. To stop a run from outside, cancel the DBOS workflow. DBOS aborts the run with its own exception rather than an `asyncio.CancelledError` (`DBOSWorkflowCancelledError`, or `DBOSWorkflowConflictIDError` on dbos 3.2 and later), but the run’s state is attached to it all the same, so catching it inside the workflow and calling [`RunCancelled.from_cancellation()`](https://pydantic.dev/docs/ai/api/pydantic-ai/exceptions/#pydantic_ai.exceptions.RunCancelled.from_cancellation) recovers the [message history after cancellation](https://pydantic.dev/docs/ai/core-concepts/agent/#message-history-after-cancellation). The `DBOSAwaitedWorkflowCancelledError` the workflow’s caller receives doesn’t carry it, as DBOS builds that error from the workflow’s recorded status.

`Agent.run_stream_sync()` is not for workflow code: it requires no running event loop and wraps `run_stream()`. Under [`DBOSDurability`](https://pydantic.dev/docs/ai/api/pydantic-ai/durable_exec/#pydantic_ai.durable_exec.dbos.DBOSDurability), use the buffered async streaming APIs above or `Agent.run()` with an event stream handler. Outside a workflow, an agent with `DBOSDurability` behaves like a normal agent, so `run_stream_sync()` works as usual. (Wrapper `DBOSAgent` forbids `run_stream` inside workflows — use `run` + event stream handler there.)

A run’s [`timeout=`](https://pydantic.dev/docs/ai/core-concepts/timeouts/#bounding-a-whole-run) works inside a workflow. The run’s start time is recorded as a `{name}.run_start_time` step, so a recovered workflow keeps the deadline the run started with, and may find it has already passed. When the deadline passes, the run raises [`RunTimedOut`](https://pydantic.dev/docs/ai/api/pydantic-ai/exceptions/#pydantic_ai.exceptions.RunTimedOut) in workflow code, where you can catch it. The deprecated `DBOSAgent` rejects `timeout=`.

The deadline bounds the agent run, not the workflow. To bound the whole workflow as well, start it inside DBOS’s `SetWorkflowTimeout`, which cancels the workflow when it expires.

When a provider pauses a model turn mid-flight (Anthropic `pause_turn`) or runs it as a server-side job that’s polled until it’s ready ([OpenAI background mode](https://pydantic.dev/docs/ai/models/openai/#background-mode)), each segment runs in a separate model request step, and the wait between polls is a durable `DBOS.sleep` step, so recovery skips completed waits. The suspended [`ModelResponse`](https://pydantic.dev/docs/ai/api/pydantic-ai/messages/#pydantic_ai.messages.ModelResponse) and background job ID are checkpointed between segments, while the final response is merged and usage is recorded once. A [`message_history`](https://pydantic.dev/docs/ai/core-concepts/message-history/) ending in a suspended response is passed to the first step. Size step timeouts for one provider round trip. If an error abandons a suspended job, its provider teardown runs in a dedicated cancellation step.

Under DBOS, tools are executed in parallel by default to minimize latency. To guarantee deterministic replay and reliable recovery, DBOS waits for all parallel tool calls to complete before emitting events **in order**. It’s equivalent to the behavior of [`with agent.parallel_tool_call_execution_mode('parallel_ordered_events')`](https://pydantic.dev/docs/ai/api/pydantic-ai/agent/#pydantic_ai.agent.AbstractAgent.parallel_tool_call_execution_mode).

If you prefer strict ordering, you can configure the agent to run tools sequentially by setting `parallel_execution_mode='sequential'` on [`DBOSDurability`](https://pydantic.dev/docs/ai/api/pydantic-ai/durable_exec/#pydantic_ai.durable_exec.dbos.DBOSDurability). A run with a [workspace](https://pydantic.dev/docs/ai/core-concepts/workspace/) always runs its tool calls sequentially.

Pass every executing toolset that needs durable wrapping to the agent constructor so its steps are registered before the workflow runs. This includes [`DynamicToolset`](https://pydantic.dev/docs/ai/api/pydantic-ai/toolsets/#pydantic_ai.toolsets.DynamicToolset): give it an explicit `id` and pass it to `Agent(toolsets=[...])`. The [`@agent.toolset`](https://pydantic.dev/docs/ai/api/pydantic-ai/agent/#pydantic_ai.agent.Agent.toolset) decorator registers after the engine’s durable units were created, so under [`DBOSDurability`](https://pydantic.dev/docs/ai/api/pydantic-ai/durable_exec/#pydantic_ai.durable_exec.dbos.DBOSDurability) using it inside a workflow raises a `UserError`. The deprecated `DBOSAgent` doesn’t run this check: inside a workflow it runs the step-wrapped toolset list frozen at wrap time, so a toolset registered that late is silently left out of the run.

Additional toolsets can be passed per run via `agent.run(toolsets=...)`. Non-executing toolsets like [`ExternalToolset`](https://pydantic.dev/docs/ai/api/pydantic-ai/toolsets/#pydantic_ai.toolsets.ExternalToolset), and [`FunctionToolset`](https://pydantic.dev/docs/ai/api/pydantic-ai/toolsets/#pydantic_ai.toolsets.FunctionToolset)s whose tools DBOS runs inline, are supported. [`MCPToolset`](https://pydantic.dev/docs/ai/api/pydantic-ai/mcp/#pydantic_ai.mcp.MCPToolset)s and dynamic toolsets passed at runtime raise a `UserError`.

Toolsets swapped in with [`agent.override(toolsets=...)`](https://pydantic.dev/docs/ai/api/pydantic-ai/agent/#pydantic_ai.agent.AbstractAgent.override) inside a workflow are held to the same rule, as they also arrive after the agent’s steps were registered. A toolset added at runtime also cannot reuse the `id` of one the agent was constructed with, as the `id` is what identifies which registered toolset’s step a tool call is dispatched to.

You can customize DBOS step behavior, such as retries, by passing [`StepConfig`](https://pydantic.dev/docs/ai/api/pydantic-ai/durable_exec/#pydantic_ai.durable_exec.dbos.StepConfig) objects to the [`DBOSDurability`](https://pydantic.dev/docs/ai/api/pydantic-ai/durable_exec/#pydantic_ai.durable_exec.dbos.DBOSDurability) constructor:

*   `mcp_step_config`: The DBOS step config to use for MCP server communication. No retries if omitted.
*   `model_step_config`: The DBOS step config to use for model request steps. No retries if omitted. The model request step carries the [durable-execution retry layer](https://pydantic.dev/docs/ai/core-concepts/retries/#the-layers) — see [Retry multiplication](https://pydantic.dev/docs/ai/core-concepts/retries/#retry-multiplication) for how `StepConfig` retries stack with the SDK client’s and transport’s retries.
*   `event_stream_handler_step_config`: The DBOS step config to use for event stream handler steps (`DBOSDurability` only). No retries if omitted.

Unlike the [Temporal](https://pydantic.dev/docs/ai/capabilities/durable_execution/temporal/#per-tool-activity-config) and [Prefect](https://pydantic.dev/docs/ai/capabilities/durable_execution/prefect/#tool-wrapping) integrations, DBOS takes no per-tool config: tool metadata (a `'dbos'` key or otherwise) is ignored, and there’s no way to opt an individual tool out of step wrapping.

For custom tools, you can annotate them directly with [`@DBOS.step`](https://docs.dbos.dev/python/reference/decorators#step) or [`@DBOS.workflow`](https://docs.dbos.dev/python/reference/decorators#workflow) decorators as needed. These decorators have no effect outside DBOS workflows, so tools remain usable in non-DBOS agents.

On top of the automatic retries for request failures that DBOS will perform, Pydantic AI and various provider API clients also have their own request retry logic. Enabling these at the same time may cause the request to be retried more often than expected, with improper `Retry-After` handling.

When using DBOS, it’s recommended to not use [transport retries](https://pydantic.dev/docs/ai/core-concepts/retries/#transport-retries) and to turn off your provider API client’s own retry logic, for example by setting `max_retries=0` on a [custom `OpenAIProvider` API client](https://pydantic.dev/docs/ai/models/openai/#custom-openai-client).

You can customize DBOS’s retry policy using [step configuration](https://ai.pydantic.dev/durable_execution/dbos/#step-configuration).

DBOS has no selective non-retryable-exception support, so if you enable step retries (`retries_allowed`), framework misconfiguration errors like `UserError` are retried along with everything else. The Temporal and Prefect integrations mark those non-retryable; on DBOS, expect a misconfigured agent to burn its full retry budget before failing.

DBOS can be configured to generate OpenTelemetry spans for each workflow and step execution, and Pydantic AI emits spans for each agent run, model request, and tool invocation. You can send these spans to [Pydantic Logfire](https://pydantic.dev/docs/ai/integrations/logfire/) to get a full, end-to-end view of what’s happening in your application.

See the [DBOS docs](https://docs.dbos.dev/python/tutorials/logging-and-tracing) for logging and tracing details.
