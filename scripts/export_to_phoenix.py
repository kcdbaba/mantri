#!/usr/bin/env python3
"""
export_to_phoenix.py

Reads spans from local Phoenix and pushes them to a remote Phoenix instance.

Usage:
    # Basic (remote Phoenix without auth)
    python scripts/export_to_phoenix.py https://example.com/developer/phoenix

    # With nginx basic auth on the droplet
    python scripts/export_to_phoenix.py https://example.com/developer/phoenix --user admin --password secret

    # Filter by project name
    python scripts/export_to_phoenix.py https://example.com/developer/phoenix --project mantri

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
        help="Remote Phoenix endpoint URL (e.g. https://example.com/developer/phoenix)",
    )
    parser.add_argument(
        "--local-url",
        default=os.environ.get("PHOENIX_LOCAL_ENDPOINT", "http://localhost:6006"),
        help="Local Phoenix endpoint (default: $PHOENIX_LOCAL_ENDPOINT or http://localhost:6006)",
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
        default="",
        help="Filter spans by project name (optional)",
    )

    args = parser.parse_args()

    try:
        import phoenix as px
    except ImportError:
        print("Error: phoenix (arize-phoenix) is not installed.", file=sys.stderr)
        print("  pip install arize-phoenix", file=sys.stderr)
        sys.exit(1)

    # ── Read spans from local Phoenix ─────────────────────────────────────────
    print(f"Connecting to local Phoenix at {args.local_url} ...")
    local_client = px.Client(endpoint=args.local_url)

    try:
        if args.project:
            spans_df = local_client.get_spans_dataframe(project_name=args.project)
        else:
            spans_df = local_client.get_spans_dataframe()
    except Exception as e:
        print(f"Error reading spans from local Phoenix: {e}", file=sys.stderr)
        sys.exit(1)

    if spans_df is None or spans_df.empty:
        print("No spans found in local Phoenix. Nothing to export.")
        sys.exit(0)

    n_spans = len(spans_df)
    print(f"Read {n_spans} spans from local Phoenix.")

    # ── Connect to remote Phoenix ─────────────────────────────────────────────
    remote_url = args.remote_url.rstrip("/")
    remote_headers = {}
    if args.user and args.password:
        remote_headers = _make_auth_header(args.user, args.password)
        print("Using basic auth for remote Phoenix.")

    print(f"Connecting to remote Phoenix at {remote_url} ...")
    remote_client = px.Client(endpoint=remote_url, headers=remote_headers)

    # ── Push spans to remote Phoenix ──────────────────────────────────────────
    try:
        # Use log_traces with the spans dataframe
        # Phoenix client supports uploading spans via the OTLP-compatible ingest
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        # Set up OTLP exporter pointing at remote Phoenix
        otlp_endpoint = f"{remote_url}/v1/traces"
        exporter = OTLPSpanExporter(
            endpoint=otlp_endpoint,
            headers=remote_headers,
        )

        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        tracer = provider.get_tracer("mantri-export")

        # Re-export each span as a new trace to the remote instance
        exported = 0
        for _, row in spans_df.iterrows():
            span_name = row.get("name", "exported_span")
            with tracer.start_as_current_span(span_name) as span:
                # Copy key attributes from the original span
                for col in spans_df.columns:
                    val = row[col]
                    if val is not None and col not in ("name", "context.span_id", "context.trace_id"):
                        try:
                            span.set_attribute(f"export.{col}", str(val))
                        except Exception:
                            pass
                exported += 1

        provider.force_flush()
        provider.shutdown()
        print(f"Exported {exported} spans to remote Phoenix at {remote_url}")

    except ImportError:
        # Fallback: if OTLP dependencies are not available, try the simpler
        # dataset upload approach
        print("OTLP exporter not available, using dataset upload fallback...")
        try:
            dataset_name = f"export-{args.project or 'default'}"
            remote_client.upload_dataset(
                dataset_name=dataset_name,
                dataframe=spans_df,
            )
            print(f"Uploaded {n_spans} spans as dataset '{dataset_name}' to remote Phoenix.")
        except Exception as e:
            print(f"Error uploading to remote Phoenix: {e}", file=sys.stderr)
            sys.exit(1)

    except Exception as e:
        print(f"Error exporting spans to remote Phoenix: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
