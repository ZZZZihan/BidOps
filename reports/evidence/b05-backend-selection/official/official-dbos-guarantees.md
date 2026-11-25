Title: Workflows | DBOS Docs

URL Source: https://docs.dbos.dev/python/tutorials/workflow-tutorial

Published Time: Thu, 22 Oct 2026 14:29:30 UTC

Markdown Content:
Workflows provide **durable execution** so you can write programs that are **resilient to any failure**. Workflows help you write fault-tolerant background tasks, data processing pipelines, AI agents, and more.

You can make a function a workflow by annotating it with [`@DBOS.workflow()`](https://docs.dbos.dev/python/reference/decorators#workflow). Workflows call [steps](https://docs.dbos.dev/python/tutorials/step-tutorial), which are Python functions annotated with [`@DBOS.step()`](https://docs.dbos.dev/python/reference/decorators#step). If a workflow is interrupted for any reason, DBOS automatically recovers its execution from the last completed step.

Here's an example of a workflow:

`@DBOS.step()def step_one():    print("Step one completed!")@DBOS.step()def step_two():    print("Step two completed!")@DBOS.workflow()def workflow():    step_one()    step_two()`

## Starting Workflows In The Background[​](https://docs.dbos.dev/python/tutorials/workflow-tutorial#starting-workflows-in-the-background "Direct link to Starting Workflows In The Background")

One common use-case for workflows is building reliable background tasks that keep running even when the program is interrupted, restarted, or crashes. You can use [`DBOS.start_workflow`](https://docs.dbos.dev/python/reference/contexts#start_workflow) to start a workflow in the background. If you start a workflow this way, it returns a [workflow handle](https://docs.dbos.dev/python/reference/workflow_handles), from which you can access information about the workflow or wait for it to complete and retrieve its result.

Here's an example:

`@DBOS.workflow()def background_task(input):    # ...    return output# Start the background taskhandle: WorkflowHandle = DBOS.start_workflow(background_task, input)# Wait for the background task to complete and retrieve its result.output = handle.get_result()`

After starting a workflow in the background, you can use [`DBOS.retrieve_workflow`](https://docs.dbos.dev/python/reference/contexts#retrieve_workflow) to retrieve a workflow's handle from its ID. You can also retrieve a workflow's handle from outside of your DBOS application with [`DBOSClient.retrieve_workflow`](https://docs.dbos.dev/python/reference/client#retrieve_workflow).

If you need to run many workflows in the background and manage their concurrency or flow control, you can also use [DBOS queues](https://docs.dbos.dev/python/tutorials/queue-tutorial).

## Workflow IDs and Idempotency[​](https://docs.dbos.dev/python/tutorials/workflow-tutorial#workflow-ids-and-idempotency "Direct link to Workflow IDs and Idempotency")

Every time you execute a workflow, that execution is assigned a unique ID, by default a [UUID](https://en.wikipedia.org/wiki/Universally_unique_identifier). You can access this ID through the [`DBOS.workflow_id`](https://docs.dbos.dev/python/reference/contexts#workflow_id) context variable. Workflow IDs are useful for communicating with workflows and developing interactive workflows.

You can set the workflow ID of a workflow with [`SetWorkflowID`](https://docs.dbos.dev/python/reference/contexts#setworkflowid). Workflow IDs must be **globally unique** for your application. An assigned workflow ID acts as an idempotency key: if a workflow is called multiple times with the same ID, it executes only once. This is useful if your operations have side effects like making a payment or sending an email. For example:

`@DBOS.workflow()def example_workflow():    DBOS.logger.info(f"I am a workflow with ID {DBOS.workflow_id}")with SetWorkflowID("very-unique-id"):    example_workflow()`

By default, if you start a workflow with an ID that is already in use, DBOS returns a handle to (or, for a direct call, the result of) the existing workflow instead of starting a new one. To instead raise an error when a workflow ID is already in use, set `workflow_id_reuse_policy="reject"` in [`SetWorkflowID`](https://docs.dbos.dev/python/reference/contexts#setworkflowid):

`from dbos import error as dboserrortry:    with SetWorkflowID("very-unique-id", workflow_id_reuse_policy="reject"):        example_workflow()except dboserror.DBOSWorkflowIDInUseError:    # A workflow with this ID already exists    ...`

## Determinism[​](https://docs.dbos.dev/python/tutorials/workflow-tutorial#determinism "Direct link to Determinism")

Workflows are in most respects normal Python functions. They can have loops, branches, conditionals, and so on. However, a workflow function must be **deterministic**: if called multiple times with the same inputs, it should invoke the same steps with the same inputs in the same order (given the same return values from those steps). If you need to perform a non-deterministic operation like accessing the database, calling a third-party API, generating a random number, or getting the local time, you shouldn't do it directly in a workflow function. Instead, you should do non-deterministic operations in [steps](https://docs.dbos.dev/python/tutorials/step-tutorial).

For example, **don't do this**:

`@DBOS.workflow()def example_workflow():    choice = random.randint(0, 1)    if choice == 0:        step_one()    else:        step_two()`

Do this instead:

`@DBOS.step()def generate_choice():    return random.randint(0, 1)@DBOS.workflow()def example_workflow(friend: str):    choice = generate_choice()    if choice == 0:        step_one()    else:        step_two()`

If DBOS detects that a single execution of a workflow recorded different results for the same step, it raises a `DBOSStepNondeterminismError`, indicating the workflow is not deterministic.

## Workflow Timeouts[​](https://docs.dbos.dev/python/tutorials/workflow-tutorial#workflow-timeouts "Direct link to Workflow Timeouts")

You can set a timeout for a workflow with [`SetWorkflowTimeout`](https://docs.dbos.dev/python/reference/contexts#setworkflowtimeout). When the timeout expires, the workflow **and all its children** are cancelled. Cancelling a workflow sets its status to `CANCELLED` and preempts its execution at the beginning of its next step. To cancel an executing async step immediately rather than waiting for it to complete, mark the step as [`preemptible`](https://docs.dbos.dev/python/reference/decorators#step).

Timeouts are **start-to-completion**: if a workflow is enqueued, the timeout does not begin until the workflow is dequeued and starts execution. Also, timeouts are **durable**: they are stored in the database and persist across restarts, so workflows can have very long timeouts.

Example syntax:

`@DBOS.workflow()def example_workflow():    ...# If the workflow does not complete within 10 seconds, it times out and is cancelledwith SetWorkflowTimeout(10):    example_workflow()`

## Durable Sleep[​](https://docs.dbos.dev/python/tutorials/workflow-tutorial#durable-sleep "Direct link to Durable Sleep")

You can use [`DBOS.sleep()`](https://docs.dbos.dev/python/reference/contexts#sleep) to put your workflow to sleep for any period of time. This sleep is **durable**—DBOS saves the wakeup time in the database so that even if the workflow is interrupted and restarted multiple times while sleeping, it still wakes up on schedule.

Sleeping is useful for scheduling a workflow to run in the future (even days, weeks, or months from now). For example:

`@DBOS.workflow()def schedule_task(time_to_sleep, task):  # Durably sleep for some time before running the task  DBOS.sleep(time_to_sleep)  run_task(task)`

## Debouncing Workflows[​](https://docs.dbos.dev/python/tutorials/workflow-tutorial#debouncing-workflows "Direct link to Debouncing Workflows")

You can debounce workflows to delay their execution until some time has passed since the workflow has last been called. This is useful for preventing wasted work when a workflow may be triggered multiple times in quick succession. For example, if a user is editing an input field, you can debounce their changes to execute a processing workflow only after they haven't edited the field for some time:

`@DBOS.workflow()def process_input(user_input):    ...# Each time a user submits a new input, debounce the process_input workflow.# The workflow will wait until 60 seconds after the user stops submitting new inputs,# then process the last input submitted.debouncer = Debouncer.create(process_input)def on_user_input_submit(user_id, user_input):    debounce_key = user_id    debounce_period_sec = 60    debouncer.debounce(debounce_key, debounce_period_sec, user_input)`

See the [debouncing reference](https://docs.dbos.dev/python/reference/contexts#debouncing) for more details.

## Coroutine (Async) Workflows[​](https://docs.dbos.dev/python/tutorials/workflow-tutorial#coroutine-async-workflows "Direct link to Coroutine (Async) Workflows")

Coroutines (functions defined with `async def`, also known as async functions) can also be DBOS workflows. Coroutine workflows may invoke [coroutine steps](https://docs.dbos.dev/python/tutorials/step-tutorial#coroutine-steps) via [await expressions](https://docs.python.org/3/reference/expressions.html#await). You should start coroutine workflows using [`DBOS.start_workflow_async`](https://docs.dbos.dev/python/reference/contexts#start_workflow_async) and enqueue them using [`DBOS.enqueue_workflow_async`](https://docs.dbos.dev/python/reference/contexts#enqueue_workflow_async). Calling a coroutine workflow or starting it with `DBOS.start_workflow_async` always runs it in the same event loop as its caller, but a workflow enqueued with `DBOS.enqueue_workflow_async` is started by DBOS in the event loop in which `DBOS.launch()` was called (if that loop is still running) or otherwise in a separate background event loop. Additionally, coroutine workflows should use the asynchronous versions of the workflow [communication](https://docs.dbos.dev/python/tutorials/workflow-communication) context methods.

`@DBOS.step()async def example_step():    async with aiohttp.ClientSession() as session:        async with session.get("https://example.com") as response:            return await response.text()@DBOS.workflow()async def example_workflow(friend: str):    await DBOS.sleep_async(10)    body = await example_step()    return body`

### Running Async Steps In Parallel[​](https://docs.dbos.dev/python/tutorials/workflow-tutorial#running-async-steps-in-parallel "Direct link to Running Async Steps In Parallel")

Initiating several concurrent steps in an `async` workflow, followed by awaiting them with `asyncio.gather(..., return_exceptions=True)`, is valid as long as the steps are started in a **deterministic order**. For example, the following is allowed:

`# Start steps in a deterministic order (step1, step2, step3, step4),    # then await them all together.    # Collects exceptions instead of raising immediately    results = await asyncio.gather(        step1("arg1"),        step2("arg2"),        step3("arg3"),        step4("arg4"),        return_exceptions=True,    )    return results`

This is allowed because each step is started in a well-defined sequence before awaiting.

By contrast, the following is not allowed:

`async def seq_a():        await step1("arg1")        await step2("arg3")    async def seq_b():        await step3("arg2")        await step4("arg4")    results = await asyncio.gather(seq_a(), seq_b(), return_exceptions=True)    return results`

Here, `step2` and `step4` may be started in either order since their execution depends on the relative time taken by `step1` and `step3`.

If you need to run sequences of operations concurrently, start child workflows and await their results, rather than interleaving step execution inside a single workflow.

For proper error handling, when using `asyncio.gather()`, specify `return_exceptions=True`. Without `return_exceptions=True`, `gather` will raise any exception immediately and stop awaiting the rest of the tasks. If one of the remaining tasks later fails, its exception may go unobserved. Instead, prefer `asyncio.gather(..., return_exceptions=True)`, which safely waits for all tasks to complete and reports their outcomes.

You can also use [`DBOS.asyncio_wait`](https://docs.dbos.dev/python/reference/contexts#asyncio_wait), a durable wrapper around [`asyncio.wait`](https://docs.python.org/3/library/asyncio-task.html#asyncio.wait), to process tasks as they complete:

`pending = [        step1("arg1"),        step2("arg2"),        step3("arg3"),        step4("arg4"),    ]    # Process each result as it completes    while pending:        done, pending = await DBOS.asyncio_wait(            pending, return_when=asyncio.FIRST_COMPLETED        )        for task in done:            result = task.result()            DBOS.logger.info(f"Completed with result: {result}")`

## Workflow Guarantees[​](https://docs.dbos.dev/python/tutorials/workflow-tutorial#workflow-guarantees "Direct link to Workflow Guarantees")

Workflows provide the following guarantees. These guarantees assume that the application and database may crash and go offline at any point in time, but are always restarted and return online.

1.   Workflows always run to completion. If a DBOS process is interrupted while executing a workflow and restarts, it resumes the workflow from the last completed step.
2.   [Steps](https://docs.dbos.dev/python/tutorials/step-tutorial) are tried _at least once_ but are never re-executed after they complete. If a failure occurs inside a step, the step may be retried, but once a step has completed, it will never be re-executed.
3.   [Transactions](https://docs.dbos.dev/python/tutorials/transaction-tutorial) commit _exactly once_. Once a workflow commits a transaction, it will never retry that transaction.

If an exception is thrown from a workflow, the workflow terminates—DBOS records the exception, sets the workflow status to `ERROR`, and does not recover the workflow. This is because uncaught exceptions are assumed to be nonrecoverable. If your workflow performs operations that may transiently fail (for example, sending HTTP requests to unreliable services), those should be performed in [steps with configured retries](https://docs.dbos.dev/python/tutorials/step-tutorial#configurable-retries). DBOS provides [tooling](https://docs.dbos.dev/python/tutorials/workflow-management) to help you identify failed workflows and examine the specific uncaught exceptions.
