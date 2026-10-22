Title: Self-hosted Temporal Service guide | Temporal Documentation

URL Source: https://docs.temporal.io/self-hosted-guide

Markdown Content:
*   [](https://docs.temporal.io/)
*   [Deploy to production](https://docs.temporal.io/production-deployment)
*   Self-host

Welcome to the self-hosted Temporal Service guide. This guide shows you how to self-host open source infrastructure software that orchestrates your durable applications.

Do you need a production Temporal Service?

If you're still developing and testing your application locally, you may not need a production Temporal Service. Use the [Temporal CLI development server](https://docs.temporal.io/cli/command-reference/server#start-dev) — a single binary with no external dependencies:

`temporal server start-dev`

This starts a complete Temporal Service with Web UI on your local machine. We recommend this for local development regardless of whether you plan to use Temporal Cloud or self-host in production. See the [Temporal CLI server](https://docs.temporal.io/cli/command-reference/server) page for configuration options.

## Plan and deploy your service[​](https://docs.temporal.io/self-hosted-guide#plan-and-deploy-your-service "Direct link to Plan and deploy your service")

*   [Deployment](https://docs.temporal.io/self-hosted-guide/deployment): Choose a deployment approach (Docker, Kubernetes, or manual) and set up a production-ready Temporal Service.
*   [Embedded server](https://docs.temporal.io/self-hosted-guide/embedded-server): Run Temporal in-process as a Go library for local development and testing scenarios.
*   [Defaults](https://docs.temporal.io/self-hosted-guide/defaults): Review platform limits and default settings that can affect Workflow and Activity behavior.
*   [Production checklist](https://docs.temporal.io/self-hosted-guide/production-checklist): Validate readiness for scale, reliability, operations, and long-term maintainability.

## Operate your self-hosted service[​](https://docs.temporal.io/self-hosted-guide#operate-your-self-hosted-service "Direct link to Operate your self-hosted service")

*   [Namespaces](https://docs.temporal.io/self-hosted-guide/namespaces): Create and manage Namespace isolation, retention, and related configuration.
*   [Security](https://docs.temporal.io/self-hosted-guide/security): Configure TLS/mTLS, authentication, authorization, and related hardening controls.
*   [Monitoring](https://docs.temporal.io/self-hosted-guide/monitoring): Collect and visualize service and SDK metrics to troubleshoot and track health.
*   [Visibility](https://docs.temporal.io/self-hosted-guide/visibility): Configure Visibility storage so you can list, filter, and search Workflow Executions.
*   [Upgrading server](https://docs.temporal.io/self-hosted-guide/upgrade-server#upgrade-server): Perform safe, sequential server and schema upgrades.

## Protect data and enable advanced features[​](https://docs.temporal.io/self-hosted-guide#protect-data-and-enable-advanced-features "Direct link to Protect data and enable advanced features")

*   [Data encryption](https://docs.temporal.io/production-deployment/data-encryption): Use Payload Codecs and Codec Server patterns to protect sensitive Workflow data.
*   [Archival](https://docs.temporal.io/self-hosted-guide/archival): Move closed Workflow Histories and Visibility records to blob storage for longer retention.
*   [Multi-Cluster Replication](https://docs.temporal.io/self-hosted-guide/multi-cluster-replication): Replicate Workflow state across clusters for failover and disaster recovery.
*   [Temporal Nexus](https://docs.temporal.io/production-deployment/self-hosted-guide/nexus): Enable Nexus in self-hosted environments to connect Temporal Applications across boundaries.

[Previous Production deployments](https://docs.temporal.io/production-deployment)[Next Deployment](https://docs.temporal.io/self-hosted-guide/deployment)
