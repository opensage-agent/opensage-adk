import os
import sys

from neo4j import GraphDatabase


class SyncNeo4jClient:
    def __init__(self, uri, user, password, database="neo4j"):
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database

    def verify_connection(self):
        try:
            self._driver.verify_connectivity()
            return True
        except Exception:
            return False

    def run_query(self, query, params=None):
        if params is None:
            params = {}
        with self._driver.session(database=self._database) as session:
            result = session.run(query, params)
            # Consuming result to a list of dicts to be compatible with previous logic
            return [record.data() for record in result]

    def close(self):
        self._driver.close()


class Neo4jUtils:
    @staticmethod
    def create_client(host, port, user, password, database="neo4j"):
        uri = f"bolt://{host}:{port}"
        return SyncNeo4jClient(uri, user, password, database)

    @staticmethod
    def get_client_from_args(args):
        # Helper to extract args and create client
        # Priority: command line args > environment variables > defaults
        host = (
            getattr(args, "neo4j_host", None)
            or os.environ.get("NEO4J_HOST")
            or "127.0.0.1"
        )
        port_str = getattr(args, "neo4j_port", None) or os.environ.get("NEO4J_PORT")
        port = int(port_str) if port_str else 7687
        user = (
            getattr(args, "neo4j_user", None) or os.environ.get("NEO4J_USER") or "neo4j"
        )
        password = (
            getattr(args, "neo4j_password", None)
            or os.environ.get("NEO4J_PASSWORD")
            or "callgraphn4j!"
        )
        database = getattr(args, "neo4j_database", "neo4j")

        if not host:
            raise ValueError("Neo4j host not provided")

        client = Neo4jUtils.create_client(host, port, user, password, database)
        client.verify_connection()
        return client


def add_neo4j_args(parser):
    """Add Neo4j connection arguments to argument parser.

    These arguments are optional. If not provided, values will be read from
    environment variables (NEO4J_HOST, NEO4J_PORT, NEO4J_USER, NEO4J_PASSWORD)
    set in ~/.bashrc by Neo4jInitializer.
    """
    group = parser.add_argument_group("Neo4j Connection")
    group.add_argument(
        "--neo4j-host",
        help="Neo4j Host IP (defaults to NEO4J_HOST env var)",
    )
    group.add_argument(
        "--neo4j-port",
        help="Neo4j Bolt Port (defaults to NEO4J_PORT env var or 7687)",
        type=int,
    )
    group.add_argument(
        "--neo4j-user",
        help="Neo4j User (defaults to NEO4J_USER env var or 'neo4j')",
    )
    group.add_argument(
        "--neo4j-password",
        help="Neo4j Password (defaults to NEO4J_PASSWORD env var)",
    )
