#!/usr/bin/env python3
"""
export_to_phoenix.py

Reads spans from local Phoenix and pushes them to a remote Phoenix instance.

Usage:
    # With nginx basic auth on the droplet
    python scripts/export_to_phoenix.py http://152.42.156.128/developer/phoenix --user guest --password guestpasswd

    # Custom project name (default: mantri)
    python scripts/export_to_phoenix.py http://example.com/developer/phoenix --project my-project

Environment variables:
    PHOENIX_LOCAL_ENDPOINT  — local Phoenix URL (default: http://localhost:6006)
    PHOENIX_REMOTE_USER     — basic auth username (alternative to --user)
    PHOENIX_REMOTE_PASSWORD — basic auth password (alternative to --password)
"""

import argparse
import base64
import os
import sys


def _make_auth_header(user: str, password: str) -> dict[str, str]:
    """Build HTTP Basic Auth header."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def main():
    parser = argparse.ArgumentParser(
        description="Export spans from local Phoenix to a remote Phoenix instance"
    )
    parser.add_argument(
        "remote_url",
        help="Remote Phoenix endpoint URL",
    )
    parser.add_argument(
        "--local-url",
        default=os.environ.get("PHOENIX_LOCAL_ENDPOINT", "http://localhost:6006"),
        help="Local Phoenix endpoint (default: http://localhost:6006)",
    )
    parser.add_argument(
        "--user",
        default=os.environ.get("PHOENIX_REMOTE_USER", ""),
        help="Basic auth username for remote Phoenix",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("PHOENIX_REMOTE_PASSWORD", ""),
        help="Basic auth password for remote Phoenix",
    )
    parser.add_argument(
        "--project",
        default="mantri",
        help="Target project name on remote Phoenix (default: mantri)",
    )

    args = parser.parse_args()

    try:
        import phoenix as px
    except ImportError:
        print("Error: arize-phoenix is not installed.", file=sys.stderr)
        sys.exit(1)

    # ── Read spans from local Phoenix ─────────────────────────────────────
    print(f"Connecting to local Phoenix at {args.local_url} ...")
    local_client = px.Client(endpoint=args.local_url)

    try:
        spans_df = local_client.get_spans_dataframe()
    except Exception as e:
        print(f"Error reading spans from local Phoenix: {e}", file=sys.stderr)
        sys.exit(1)

    if spans_df is None or spans_df.empty:
        print("No spans found in local Phoenix. Nothing to export.")
        sys.exit(0)

    n_spans = len(spans_df)
    print(f"Read {n_spans} spans from local Phoenix.")

    # ── Push spans to remote Phoenix ──────────────────────────────────────
    remote_url = args.remote_url.rstrip("/")
    remote_headers = {}
    if args.user and args.password:
        remote_headers = _make_auth_header(args.user, args.password)
        print("Using basic auth for remote Phoenix.")

    project_name = args.project

    try:
        from phoenix.otel import register

        # phoenix.otel.register() correctly sets project.name via OTEL
        # resource attributes that Phoenix uses for project assignment
        tracer_provider = register(
            project_name=project_name,
            endpoint=f"{remote_url}/v1/traces",
            headers=remote_headers,
        )
        tracer = tracer_provider.get_tracer("mantri-export")

        exported = 0
        for _, row in spans_df.iterrows():
            span_name = row.get("name", "exported_span")
            with tracer.start_as_current_span(span_name) as span:
                for col in spans_df.columns:
                    val = row[col]
                    if val is not None and col not in (
                        "name", "context.span_id", "context.trace_id",
                        "parent_id", "start_time", "end_time", "events",
                    ):
                        try:
                            span.set_attribute(f"export.{col}", str(val))
                        except Exception:
                            pass
                exported += 1

        tracer_provider.force_flush()
        tracer_provider.shutdown()
        print(f"Exported {exported} spans to {remote_url} (project: {project_name})")

    except Exception as e:
        print(f"Error exporting spans: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
