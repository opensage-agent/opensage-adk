import argparse
import asyncio
import os
import sys

# Setup path to import common_utils
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(current_dir))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

try:
    from common_utils.joern_utils import JoernClient
except ImportError:
    # Fallback: try importing from opensage if available (for testing outside container)
    try:
        from opensage.session.joern_client import JoernClient
    except ImportError:
        print(
            "Error: JoernClient not found. Please ensure common_utils.joern_utils is available.",
            file=sys.stderr,
        )
        sys.exit(1)


async def main():
    parser = argparse.ArgumentParser(description="Run custom Joern query.")
    parser.add_argument("query", help="The Joern query string")
    args = parser.parse_args()
    query = args.query

    try:
        # Get Joern server endpoint from environment variables
        # Environment variables should be loaded from /shared/bashrc by bash_task_manager
        host = os.environ.get("JOERN_SERVER_HOST")

        if not host:
            print(
                "Error: JOERN_SERVER_HOST environment variable is not set",
                file=sys.stderr,
            )
            sys.exit(1)

        # Default to container port 8081 (not host mapping port 18087)
        port = int(os.environ.get("JOERN_SERVER_PORT", "8081"))

        client = JoernClient(server_endpoint=f"{host}:{port}")

        response = await client.aexecute(query)

        # Output response as plain text
        if response is None:
            print("No response from Joern query.")
        elif isinstance(response, str):
            print(response)
        else:
            # If response is a complex object, convert to string
            print(str(response))

    except Exception as e:
        print(f"Error: Failed to execute Joern query: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
