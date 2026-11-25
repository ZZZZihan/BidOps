Title: Configuration | DBOS Docs

URL Source: https://docs.dbos.dev/python/reference/configuration

Markdown Content:
[Skip to main content](https://docs.dbos.dev/python/reference/configuration#__docusaurus_skipToContent_fallback)

[![Image 1: DBOS Logo](https://docs.dbos.dev/img/dbos-logo.png)](https://dbos.dev/)[Docs](https://docs.dbos.dev/)[Quickstart](https://docs.dbos.dev/quickstart)[Examples](https://docs.dbos.dev/examples)[AI Agents](https://docs.dbos.dev/ai/ai-quickstart)

Search Ctrl K

[DBOS Console](https://console.dbos.dev/login-redirect)[](https://github.com/dbos-inc)

*   [Home](https://docs.dbos.dev/)
*   [Get Started](https://docs.dbos.dev/quickstart)
*   [Why DBOS?](https://docs.dbos.dev/why-dbos)
*   [DBOS Architecture](https://docs.dbos.dev/architecture)
*   [Develop with Python](https://docs.dbos.dev/python/reference/configuration#) 
    *   [Learn DBOS Python](https://docs.dbos.dev/python/programming-guide)
    *   [Add DBOS To Your App](https://docs.dbos.dev/python/integrating-dbos)
    *   [AI-Assisted Development](https://docs.dbos.dev/python/prompting)
    *   [Tutorials](https://docs.dbos.dev/python/reference/configuration#) 
    *   [Reference](https://docs.dbos.dev/python/reference/configuration#) 
        *   [DBOS Class](https://docs.dbos.dev/python/reference/dbos-class)
        *   [Configuration](https://docs.dbos.dev/python/reference/configuration)
        *   [Workflows & Steps](https://docs.dbos.dev/python/reference/decorators)
        *   [DBOS Methods & Variables](https://docs.dbos.dev/python/reference/contexts)
        *   [Queues](https://docs.dbos.dev/python/reference/queues)
        *   [Workflow Handles](https://docs.dbos.dev/python/reference/workflow_handles)
        *   [Datasources](https://docs.dbos.dev/python/reference/datasources)
        *   [DBOS Client](https://docs.dbos.dev/python/reference/client)
        *   [DBOS CLI](https://docs.dbos.dev/python/reference/cli)

    *   [Example Applications](https://docs.dbos.dev/python/reference/configuration#) 
    *   [Upgrading to 3.0](https://docs.dbos.dev/python/upgrading)

*   [Develop with TypeScript](https://docs.dbos.dev/python/reference/configuration#) 
*   [Develop with Go](https://docs.dbos.dev/python/reference/configuration#) 
*   [Develop with Java](https://docs.dbos.dev/python/reference/configuration#) 
*   [Deploy to Production](https://docs.dbos.dev/python/reference/configuration#) 
*   [DBOS Conductor](https://docs.dbos.dev/python/reference/configuration#) 
*   [Build Durable AI Agents](https://docs.dbos.dev/python/reference/configuration#) 
*   [Integrations](https://docs.dbos.dev/python/reference/configuration#) 
*   [Concepts and Explanations](https://docs.dbos.dev/python/reference/configuration#) 
*   [Example Applications](https://docs.dbos.dev/examples)
*   [Troubleshooting & FAQ](https://docs.dbos.dev/faq)

*   [](https://docs.dbos.dev/)
*   Develop with Python
*   Reference
*   Configuration

On this page

# Configuration

## Configuring DBOS[​](https://docs.dbos.dev/python/reference/configuration#configuring-dbos "Direct link to Configuring DBOS")

To configure DBOS, pass a `DBOSConfig` object to its constructor. For example:

`config: DBOSConfig = {    "name": "dbos-example",    "application_version": "0.1.0",    "system_database_url": os.environ["DBOS_SYSTEM_DATABASE_URL"],}DBOS(config=config)`

The `DBOSConfig` object has the following fields. All fields except `name` are optional.

`class DBOSConfig(TypedDict):    name: str    enable_patching: Optional[bool]    application_version: Optional[str]    executor_id: Optional[str]    system_database_url: Optional[str]    sys_db_pool_size: Optional[int]    sys_db_polling_concurrency: Optional[int]    db_engine_kwargs: Optional[Dict[str, Any]]    dbos_system_schema: Optional[str]    system_database_engine: Optional[sqlalchemy.Engine]    use_listen_notify: Optional[bool]    run_migrations: Optional[bool]    notification_listener_polling_interval_sec: Optional[float]    notification_coalesce_sec: Optional[float]    observability_query_timeout_sec: Optional[float]    sys_db_idle_transaction_timeout_sec: Optional[float]    conductor_key: Optional[str]    conductor_url: Optional[str]    conductor_executor_metadata: Optional[Dict[str, Any]]    conductor_metadata_only_mode: Optional[bool]    enable_otlp: Optional[bool]    otlp_traces_endpoints: Optional[List[str]]    otlp_logs_endpoints: Optional[List[str]]    otlp_attributes: Optional[dict[str, str]]    otel_attribute_format: Optional[Literal["legacy", "semconv"]]    log_level: Optional[str]    otlp_log_level: Optional[str]    console_log_level: Optional[str]    max_executor_threads: Optional[int]    scheduler_polling_interval_sec: Optional[float]    kafka_queue_polling_interval_sec: Optional[float]    serializer: Optional[Serializer]`

### Application Settings[​](https://docs.dbos.dev/python/reference/configuration#application-settings "Direct link to Application Settings")

*   **name**: Your application's name. It must be between 3 and 256 characters long and contain only lowercase letters, numbers, dashes, and underscores. Multiple applications (potentially in different languages) may [share a system database](https://docs.dbos.dev/explanations/sharing-a-system-database), in which case each must have a distinct name: the name identifies which application owns each workflow, queue, schedule, and application version, and applications only run their own workflows. If you rename an application, transfer ownership of its data with [`dbos rename-application`](https://docs.dbos.dev/python/reference/cli#dbos-rename-application).
*   **enable_patching**: Enable the [patching](https://docs.dbos.dev/python/tutorials/upgrading-workflows#patching) strategy for safely upgrading workflow code. Required to use [`DBOS.patch`](https://docs.dbos.dev/python/reference/contexts#patch) and [`DBOS.deprecate_patch`](https://docs.dbos.dev/python/reference/contexts#deprecate_patch), which otherwise raise a `DBOSException`.
*   **application_version**: If using the [versioning](https://docs.dbos.dev/python/tutorials/upgrading-workflows#versioning) strategy for safely upgrading workflow code, the code version for this application and its workflows.
*   **executor_id**: A unique process ID used to identify the application instance in distributed environments. If using DBOS Conductor or Cloud, this is set automatically.

### Database Connection Settings[​](https://docs.dbos.dev/python/reference/configuration#database-connection-settings "Direct link to Database Connection Settings")

*   **system_database_url**: A connection string to your system database. This is the database in which DBOS stores workflow and step state; its schema is documented [here](https://docs.dbos.dev/explanations/system-tables). This may be either Postgres or SQLite, though Postgres is recommended for production. DBOS uses this connection string to create a [SQLAlchemy engine](https://docs.sqlalchemy.org/en/20/core/engines.html). For Postgres, DBOS always connects with the `psycopg` (version 3) driver, replacing any driver specified in the connection string. A valid connection string looks like:

`postgresql://[username]:[password]@[hostname]:[port]/[database name]`

Or with SQLite:

`sqlite:///[path to database file]`

info

Passwords in connection strings must be escaped (for example with [urllib](https://docs.python.org/3/library/urllib.parse.html#urllib.parse.quote)) if they contain special characters.

If no connection string is provided, DBOS uses a SQLite database (with any dashes in the application name replaced by underscores, and an underscore prepended if the name starts with a digit):

`sqlite:///[application_name].sqlite`

*   **sys_db_pool_size**: The size of the connection pool used for the [DBOS system database](https://docs.dbos.dev/explanations/system-tables). Defaults to 20.
*   **sys_db_polling_concurrency**: The maximum number of database-backed polling reads from wait operations (such as [`get_result`](https://docs.dbos.dev/python/reference/contexts#get_result), [`recv`](https://docs.dbos.dev/python/reference/contexts#recv), [`get_event`](https://docs.dbos.dev/python/reference/contexts#get_event), and [`read_stream`](https://docs.dbos.dev/python/reference/contexts#read_stream)) that may run concurrently against the system database pool. This prevents high-fan-out polling from checking out every connection in the pool and starving control-plane operations (such as enqueue/dequeue, status writes, recovery, and cancellation). Defaults to half the `sys_db_pool_size` (minimum 1). Set to a non-positive value to disable the limit.
*   **db_engine_kwargs**: A dictionary of additional keyword arguments passed to the SQLAlchemy [create_engine](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.create_engine) call. Can be used to customize connection pool settings, timeouts, and other engine parameters.
*   **dbos_system_schema**: Postgres schema name for DBOS system tables. Defaults to `dbos`.
*   **system_database_engine**: A custom SQLAlchemy engine to use to connect to your system database. If provided, DBOS will not create an engine but use this instead.
*   **use_listen_notify**: Whether to use PostgreSQL LISTEN/NOTIFY (`True`) or polling (`False`) to await notifications and events. Defaults to `True`. Ignored in SQLite, which always uses polling. On Postgres, this setting determines which notification triggers are created with the system database, so do not change it after the system database is first created.
*   **run_migrations**: Whether to create and migrate the system database on launch. Defaults to `True`. Set to `False` for a process that must not alter the schema, such as one whose database role cannot run DDL, or a deployment that migrates out of band with [`dbos migrate`](https://docs.dbos.dev/python/reference/cli#dbos-migrate) or [`DBOS.migrate`](https://docs.dbos.dev/python/reference/dbos-class#migrate). Launch then verifies the schema instead of changing it: a system database whose DBOS tables are missing (including a SQLite file that does not exist) or behind the version this build of DBOS requires fails launch with a `DBOSInitializationError`, and a Postgres database that does not exist fails launch with a connection error. A system database ahead of the required version is accepted, so a process with migrations disabled can run alongside newer peers.
*   **notification_listener_polling_interval_sec**: Polling interval in seconds for the notification listener background process. Defaults to `1.0`; the minimum is `0.001`. Used when polling (when `use_listen_notify` is `False` or the system database is SQLite), and as the default `polling_interval_sec` of [`read_stream`](https://docs.dbos.dev/python/reference/contexts#read_stream) and [`read_stream_offset`](https://docs.dbos.dev/python/reference/contexts#read_stream_offset).
*   **notification_coalesce_sec**: Interval in seconds at which DBOS batches and sends the LISTEN/NOTIFY notifications that wake readers of [events](https://docs.dbos.dev/python/reference/contexts#get_event) and [streams](https://docs.dbos.dev/python/reference/contexts#read_stream). This bounds how long a waiting reader may be delayed and caps the rate of notifying commits regardless of write throughput. Defaults to `0.01`; the minimum is `0.001`. Only used on Postgres when `use_listen_notify` is `True`.
*   **observability_query_timeout_sec**: The statement timeout, in seconds, applied to observability queries (such as listing workflows, queued workflows, and workflow steps) on a Postgres system database, so a slow query on a large database does not hold resources indefinitely. A query that exceeds the timeout raises `DBOSQueryTimeoutError`. Defaults to 30 seconds. Set to zero or a negative value to disable the timeout.
*   **sys_db_idle_transaction_timeout_sec**: The Postgres `idle_in_transaction_session_timeout`, in seconds, set on the system database connections DBOS creates. Defaults to 60 seconds.

### Conductor Settings[​](https://docs.dbos.dev/python/reference/configuration#conductor-settings "Direct link to Conductor Settings")

*   **conductor_key**: An API key for [DBOS Conductor](https://docs.dbos.dev/conductor/overview). If provided, application connects to Conductor. API keys can be created from the [DBOS Console](https://console.dbos.dev/). Connecting to Conductor requires the `dbos-enterprise` package (`pip install "dbos[enterprise]"`).
*   **conductor_url**: The URL of the Conductor service to connect to. Only set if you are self-hosting Conductor.
*   **conductor_executor_metadata**: A JSON-serializable dictionary of metadata to associate with this executor. This metadata is sent to Conductor and displayed on the dashboard, making it easier to identify executors (e.g., by region, instance type, or deployment environment).
*   **conductor_metadata_only_mode**: If `True`, this process sends only workflow metadata to Conductor, never workflow data (inputs, outputs, errors, step outputs, events, messages, streams, or schedule context), regardless of the [metadata-only mode](https://docs.dbos.dev/conductor/overview#metadata-only-mode) setting in the Conductor console. Defaults to `False`.

### Logging and Tracing Settings[​](https://docs.dbos.dev/python/reference/configuration#logging-and-tracing-settings "Direct link to Logging and Tracing Settings")

*   **enable_otlp**: Enable DBOS OpenTelemetry [tracing](https://docs.dbos.dev/python/tutorials/logging-and-tracing), which makes DBOS create spans for all workflows and steps. To export those spans, either set `otlp_traces_endpoints`/`otlp_logs_endpoints` (DBOS runs its own built-in `TracerProvider`) or [register your own `TracerProvider`](https://docs.dbos.dev/python/tutorials/logging-and-tracing#connecting-dbos-to-your-observability-provider) before launch and let your observability provider export them. Defaults to False.
*   **otlp_traces_endpoints**: If using the built-in DBOS OpenTelemetry `TracerProvider`, a list of receivers to which to send traces.
*   **otlp_logs_endpoints**: If using the built-in DBOS OpenTelemetry `TracerProvider`, a list of receivers to which to send logs.
*   **otlp_attributes**: A set of attributes (key-value pairs) to apply to all OTLP-exported logs and traces.
*   **otel_attribute_format**: Naming convention for DBOS-emitted span attributes. Defaults to `"legacy"`, which emits the original camelCase names (`operationUUID`, `executorID`, …) for backward compatibility. Set to `"semconv"` to emit OTel-style names under the `dbos.*` namespace (`dbos.operation.workflow_id`, `dbos.executor.id`, …), which follow the [OTel attribute naming spec](https://opentelemetry.io/docs/specs/semconv/general/attribute-naming/) and avoid colliding with attributes set by other instrumentation. The flag is process-wide; user-supplied `otlp_attributes` are passed through verbatim either way.
*   **log_level**: Configure the [DBOS logger](https://docs.dbos.dev/python/tutorials/logging-and-tracing#logging) severity. Defaults to `INFO`.
*   **otlp_log_level**: Log level specifically for OTLP logging (if enabled). Must be no less severe than `log_level`. Defaults to the value of `log_level`.
*   **console_log_level**: Log level specifically for console logging. Must be no less severe than `log_level`. Defaults to the value of `log_level`.

### Execution Settings[​](https://docs.dbos.dev/python/reference/configuration#execution-settings "Direct link to Execution Settings")

*   **max_executor_threads**: The maximum number of threads in the executor thread pool used for running synchronous workflow and step functions. If unset, the pool is unbounded.

### Scheduler Settings[​](https://docs.dbos.dev/python/reference/configuration#scheduler-settings "Direct link to Scheduler Settings")

*   **scheduler_polling_interval_sec**: Polling interval in seconds for the scheduler thread to detect new [workflow schedules](https://docs.dbos.dev/python/reference/contexts#workflow-schedules). Defaults to `30.0`.

### Kafka Settings[​](https://docs.dbos.dev/python/reference/configuration#kafka-settings "Direct link to Kafka Settings")

*   **kafka_queue_polling_interval_sec**: Polling interval in seconds for the internal queues on which [Kafka consumer](https://docs.dbos.dev/python/tutorials/kafka-integration) workflows run. Defaults to `1.0`; the minimum is `0.001`. Lowering it reduces the latency between a message being enqueued and its workflow starting, but requires more frequent database polling.

### Serialization Settings[​](https://docs.dbos.dev/python/reference/configuration#serialization-settings "Direct link to Serialization Settings")

*   **serializer**: A custom serializer for the system database. See the [custom serialization reference](https://docs.dbos.dev/python/reference/contexts#custom-serialization) for details.

## DBOS Configuration File[​](https://docs.dbos.dev/python/reference/configuration#dbos-configuration-file "Direct link to DBOS Configuration File")

Some tools in the DBOS ecosystem, including [DBOS Cloud](https://docs.dbos.dev/conductor/reference/dbos-cloud/deploying-to-cloud) and the [DBOS CLI](https://docs.dbos.dev/python/reference/cli), are configured by a `dbos-config.yaml` file.

You can create a `dbos-config.yaml` with default parameters with:

`dbos init <app-name> --config`

### Configuration File Fields[​](https://docs.dbos.dev/python/reference/configuration#configuration-file-fields "Direct link to Configuration File Fields")

info

You can use environment variables for configuration values through the syntax `field: ${VALUE}`.

Each `dbos-config.yaml` file has the following fields and sections:

*   **name**: Your application's name. Must match the name supplied to the DBOS constructor.
*   **language**: The application language. Must be set to `python` for Python applications.
*   **system_database_url**: The connection string to your DBOS system database. This connection string is used by the DBOS [CLI](https://docs.dbos.dev/python/reference/cli). It has the same format as the `system_database_url` you pass to the DBOS constructor.
*   **runtimeConfig**:
    *   **start**: (required only in DBOS Cloud) The command(s) with which to start your app. Called from [`dbos start`](https://docs.dbos.dev/python/reference/cli#dbos-start), which is used to start your app in DBOS Cloud.
    *   **setup**: Setup commands to run before your application is built in DBOS Cloud. Used only in DBOS Cloud. Documentation [here](https://docs.dbos.dev/conductor/reference/dbos-cloud/application-management#customizing-microvm-setup).

### Configuration Schema File[​](https://docs.dbos.dev/python/reference/configuration#configuration-schema-file "Direct link to Configuration Schema File")

There is a schema file available for the DBOS configuration file schema [on GitHub](https://github.com/dbos-inc/dbos-transact-py/blob/main/dbos/dbos-config.schema.json). This schema file can be used to provide an improved YAML editing experience for developer tools that leverage it. For example, the Visual Studio Code [RedHat YAML extension](https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml) provides tooltips, statement completion and real-time validation for editing DBOS config files. This extension provides [multiple ways](https://github.com/redhat-developer/vscode-yaml#associating-schemas) to associate a YAML file with its schema. The easiest is to simply add a comment with a link to the schema at the top of the config file:

`# yaml-language-server: $schema=https://raw.githubusercontent.com/dbos-inc/dbos-transact-py/main/dbos/dbos-config.schema.json`

[Previous DBOS Class](https://docs.dbos.dev/python/reference/dbos-class)[Next Workflows & Steps](https://docs.dbos.dev/python/reference/decorators)

*   [Configuring DBOS](https://docs.dbos.dev/python/reference/configuration#configuring-dbos)
    *   [Application Settings](https://docs.dbos.dev/python/reference/configuration#application-settings)
    *   [Database Connection Settings](https://docs.dbos.dev/python/reference/configuration#database-connection-settings)
    *   [Conductor Settings](https://docs.dbos.dev/python/reference/configuration#conductor-settings)
    *   [Logging and Tracing Settings](https://docs.dbos.dev/python/reference/configuration#logging-and-tracing-settings)
    *   [Execution Settings](https://docs.dbos.dev/python/reference/configuration#execution-settings)
    *   [Scheduler Settings](https://docs.dbos.dev/python/reference/configuration#scheduler-settings)
    *   [Kafka Settings](https://docs.dbos.dev/python/reference/configuration#kafka-settings)
    *   [Serialization Settings](https://docs.dbos.dev/python/reference/configuration#serialization-settings)

*   [DBOS Configuration File](https://docs.dbos.dev/python/reference/configuration#dbos-configuration-file)
    *   [Configuration File Fields](https://docs.dbos.dev/python/reference/configuration#configuration-file-fields)
    *   [Configuration Schema File](https://docs.dbos.dev/python/reference/configuration#configuration-schema-file)

Follow Us

*   [LinkedIn](https://www.linkedin.com/company/dbos-inc/)
*   [Bluesky](https://bsky.app/profile/dbos.dev)
*   [Twitter/X](https://x.com/DBOS_Inc)

Learn More

*   [Blog](https://www.dbos.dev/blog)
*   [About](https://www.dbos.dev/about)

Questions or Comments?

*   [Chat with us on Discord](https://discord.gg/fMwQjeW5zg)
*   [Write to us at contact@dbos.dev](mailto:contact@dbos.dev)

[![Image 2: DBOS Logo](https://docs.dbos.dev/img/dbos-logo.png)](https://dbos.dev/)

Copyright © 2026 DBOS, Inc. | [Privacy Policy](https://www.dbos.dev/privacy)
