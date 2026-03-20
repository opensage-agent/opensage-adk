# FROM: https://github.com/joernio/cpgqls-client-python/blob/master/cpgqls_client/client.py

import asyncio

import httpx
from websockets.asyncio.client import connect


class JoernClient:
    CPGQLS_MSG_CONNECTED = "connected"
    DEFAULT_TIMEOUT = 3600

    def __init__(self, server_endpoint, auth_credentials=None):
        if server_endpoint is None:
            raise ValueError("server_endpoint cannot be None")
        if not isinstance(server_endpoint, str):
            raise ValueError("server_endpoint parameter has to be a string")

        self._endpoint = server_endpoint.rstrip("/")
        self._auth_creds = auth_credentials

    async def aexecute(self, query, timeout=DEFAULT_TIMEOUT):
        endpoint = self.connect_endpoint()
        async with connect(endpoint) as ws_conn:
            connected_msg = await ws_conn.recv()
            if connected_msg != self.CPGQLS_MSG_CONNECTED:
                exception_msg = """Received unexpected first message
                on websocket endpoint"""
                raise Exception(exception_msg)
            endpoint = self.post_query_endpoint()
            post_res = httpx.post(
                endpoint, json={"query": query}, auth=self._auth_creds
            )
            if post_res.status_code == 401:
                exception_msg = """Basic authentication failed"""
                raise Exception(exception_msg)
            elif post_res.status_code != 200:
                exception_msg = """Could not post query to the HTTP
                endpoint of the server"""
                raise Exception(exception_msg)
            await asyncio.wait_for(ws_conn.recv(), timeout=timeout)
            endpoint = self.get_result_endpoint(post_res.json()["uuid"])
            get_res = httpx.get(endpoint, auth=self._auth_creds)
            if post_res.status_code == 401:
                exception_msg = """Basic authentication failed"""
                raise Exception(exception_msg)
            elif get_res.status_code != 200:
                exception_msg = """Could not retrieve query result via the HTTP endpoint
                of the server"""
                raise Exception(exception_msg)
            return get_res.json()

    def execute(self, query, timeout=DEFAULT_TIMEOUT):
        raise NotImplementedError("Synchronous execution is not implemented yet.")

    def connect_endpoint(self):
        return "ws://" + self._endpoint + "/connect"

    def post_query_endpoint(self):
        return "http://" + self._endpoint + "/query"

    def get_result_endpoint(self, uuid):
        return "http://" + self._endpoint + "/result/" + uuid
