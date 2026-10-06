"""Offline security and tool-continuation tests; no paid/API requests."""
import asyncio
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from app.services.chatgpt_plan_service import ChatGPTPlanError, ChatGPTPlanService, SCOPE
from tradingagents.llm_clients.chatgpt_plan import ChatGPTPlanChatModel, response_input, response_message
from tradingagents.llm_clients.openai_client import OpenAIClient


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = ChatGPTPlanService(self.directory.name, "test-secret-only")

    def tearDown(self):
        self.directory.cleanup()

    def connected(self, expires=None):
        self.service.vault["profiles"]["p"] = {"client_id": "oaiapp_example", "subject": "user1", "email": "test@example.com",
            "access_token": "secret-access", "refresh_token": "secret-refresh", "id_token": "secret-id",
            "scopes": SCOPE.split(), "expires_at": expires or time.time() + 3600}
        self.service.vault["active"] = "p"
        self.service._save()

    def begin(self, profile=None):
        with patch.object(self.service, "_listener"):
            return parse_qs(urlparse(self.service.begin(profile)["authorization_url"]).query)

    def test_vault_encrypted_and_host_persists(self):
        self.connected()
        self.assertNotIn(b"secret-access", self.service.path.read_bytes())
        other = ChatGPTPlanService(self.directory.name, "test-secret-only")
        self.assertEqual(self.service.vault["host_id"], other.vault["host_id"])
        self.assertNotIn("secret-access", json.dumps(other.status()))
        self.assertNotIn("secret-id", json.dumps(other.status()))

    def test_pkce_state_and_loopback(self):
        query = self.begin()
        self.assertEqual(query["client_id"], ["dynamic_agent_client"])
        self.assertEqual(query["redirect_uri"], ["http://127.0.0.1:1455/auth/callback"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(set(query["scope"][0].split()), set(SCOPE.split()))
        with self.assertRaises(ChatGPTPlanError):
            self.service.complete({"state": ["invalid"], "code": ["fake"]})

    def test_cancelled_state_consumed_and_old_profile_preserved(self):
        self.connected()
        query = self.begin()
        with self.assertRaises(ChatGPTPlanError):
            self.service.complete({"state": query["state"], "error": ["access_denied"]})
        self.assertEqual(self.service.vault["active"], "p")
        self.assertEqual(self.service.access_token(), "secret-access")
        with self.assertRaises(ChatGPTPlanError):
            self.service.complete({"state": query["state"], "code": ["replay"]})

    def test_callback_client_and_nonce_validation(self):
        self.connected()
        query = self.begin("p")
        self.assertNotIn("agent_name_hint", query)
        self.assertEqual(query["id_token_hint"], ["secret-id"])
        with patch.object(self.service, "_token_request") as exchange:
            with self.assertRaises(ChatGPTPlanError):
                self.service.complete({"state": query["state"], "code": ["fake"], "client_id": ["other"]})
            exchange.assert_not_called()
        query = self.begin("p")
        key = Mock(key="key")
        with patch("jwt.PyJWKClient") as jwks, patch("jwt.decode", return_value={"sub": "user1", "nonce": "wrong"}):
            jwks.return_value.get_signing_key_from_jwt.return_value = key
            with self.assertRaises(ChatGPTPlanError):
                self.service._verify_identity("fake", "oaiapp_example", "expected")

    def test_expired_and_duplicate_state_rejected(self):
        query = self.begin()
        self.service.pending[query["state"][0]]["expires"] = 0
        with self.assertRaises(ChatGPTPlanError):
            self.service.complete({"state": query["state"], "code": ["x"]})
        with self.assertRaises(ChatGPTPlanError):
            self.service.complete({"state": ["one", "two"]})

    def test_real_id_token_signature_audience_issuer_expiry(self):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = {"iss": "https://auth.openai.com", "aud": "oaiapp_example", "sub": "user1",
                  "exp": int(time.time()) + 600, "nonce": "nonce"}
        key = Mock(key=private.public_key())
        with patch("jwt.PyJWKClient") as jwks:
            jwks.return_value.get_signing_key_from_jwt.return_value = key
            token = jwt.encode(claims, private, algorithm="RS256")
            self.assertEqual(self.service._verify_identity(token, "oaiapp_example", "nonce")["sub"], "user1")
            for changed in ({"aud": "wrong"}, {"iss": "https://other.example"}, {"exp": 1}, {"nonce": "wrong"}):
                with self.assertRaises(ChatGPTPlanError):
                    self.service._verify_identity(jwt.encode({**claims, **changed}, private, algorithm="RS256"), "oaiapp_example", "nonce")
            with self.assertRaises(ChatGPTPlanError):
                self.service._verify_identity(jwt.encode(claims, "invalid", algorithm="HS256"), "oaiapp_example", "nonce")

    def test_successful_exchange_grants_and_identity(self):
        query = self.begin()
        tokens = {"access_token": "new-access", "refresh_token": "new-refresh", "id_token": "new-id",
                  "scope": SCOPE, "expires_in": 3600}
        with patch.object(self.service, "_token_request", return_value=tokens) as exchange, \
             patch.object(self.service, "_verify_identity", return_value={"sub": "u", "email": "u@example.com"}):
            self.service.complete({"state": query["state"], "code": ["code"], "client_id": ["oaiapp_new"]})
        self.assertEqual(exchange.call_args.args[0]["client_id"], "oaiapp_new")
        self.assertEqual(self.service.access_token(), "new-access")

    def test_identity_without_sharing_cannot_infer(self):
        query = self.begin()
        tokens = {"access_token": "new-access", "id_token": "new-id", "scope": "openid email", "expires_in": 3600}
        with patch.object(self.service, "_token_request", return_value=tokens), \
             patch.object(self.service, "_verify_identity", return_value={"sub": "u"}):
            with self.assertRaises(ChatGPTPlanError):
                self.service.complete({"state": query["state"], "code": ["code"], "client_id": ["oaiapp_new"]})
        with self.assertRaises(ChatGPTPlanError):
            self.service.access_token()

    def test_refresh_serialized_and_rotating_token_saved(self):
        self.connected(expires=1)
        with patch.object(self.service, "_token_request", return_value={"access_token": "renewed", "refresh_token": "rotated", "expires_in": 3600}) as exchange:
            with ThreadPoolExecutor(max_workers=5) as pool:
                self.assertEqual(list(pool.map(lambda _: self.service.access_token(), range(5))), ["renewed"] * 5)
            self.assertEqual(exchange.call_count, 1)
            self.assertNotIn("scope", exchange.call_args.args[0])
        self.assertEqual(self.service.vault["profiles"]["p"]["refresh_token"], "rotated")

    def test_sign_out_clears_tokens_even_when_network_fails(self):
        import requests
        self.connected()
        with patch("requests.get", side_effect=requests.ConnectionError):
            result = self.service.sign_out("p")
        self.assertFalse(result["revoked"])
        self.assertEqual(self.service.vault["profiles"]["p"]["client_id"], "oaiapp_example")
        with self.assertRaises(ChatGPTPlanError):
            self.service.access_token()

    def stream(self, events):
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        lines = []
        for event in events:
            lines.extend(["event: " + event["type"], "data: " + json.dumps(event), ""])
        response.iter_lines.return_value = iter(lines)
        return response

    def test_stream_allowlist_completion_and_usage_error(self):
        self.connected()
        completed = {"type": "response.completed", "response": {"status": "completed", "output": []}}
        with patch("requests.post", return_value=self.stream([completed])) as send:
            self.service.response({"model": "test", "input": [], "temperature": 1, "max_output_tokens": 123, "store": True})
        body = send.call_args.kwargs["json"]
        self.assertEqual(set(body), {"model", "input", "store", "stream"})
        self.assertFalse(body["store"])
        self.assertTrue(body["stream"])
        failure = {"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}}
        with patch("requests.post", return_value=self.stream([failure])):
            with self.assertRaises(ChatGPTPlanError) as error:
                self.service.response({"model": "test", "input": []})
            self.assertEqual(error.exception.status, 429)
        with patch("requests.post", return_value=self.stream([])):
            with self.assertRaises(ChatGPTPlanError):
                self.service.response({"model": "test", "input": []})

    def test_tool_roundtrip_keeps_reasoning_and_namespace(self):
        output = [{"type": "reasoning", "id": "reason", "encrypted_content": "encrypted"},
                  {"type": "function_call", "id": "fc", "call_id": "call1", "namespace": "tradingagents", "name": "quote", "arguments": '{"symbol":"000001"}'}]
        message = response_message({"model": "test", "output": output})
        self.assertEqual(message.tool_calls[0]["name"], "quote")
        payload = response_input([SystemMessage("Research"), HumanMessage("Check a stock"), message, ToolMessage("10", tool_call_id="call1")])
        self.assertEqual(payload[0]["role"], "developer")
        self.assertEqual(payload[2:4], output)
        self.assertEqual(payload[-1]["call_id"], "call1")

    def test_stream_recovers_items_when_terminal_output_is_empty(self):
        self.connected()
        item = {"type": "function_call", "call_id": "call1", "name": "quote", "arguments": "{}"}
        done = {"type": "response.output_item.done", "output_index": 0, "item": item}
        completed = {"type": "response.completed", "response": {"status": "completed", "output": []}}
        with patch("requests.post", return_value=self.stream([done, completed])):
            result = self.service.response({"model": "test", "input": []})
        self.assertEqual(response_message(result).tool_calls[0]["name"], "quote")
        with patch("requests.post", return_value=self.stream([done])):
            with self.assertRaises(ChatGPTPlanError):
                self.service.response({"model": "test", "input": []})

    def test_adapter_factory_and_bind_tools(self):
        @tool
        def quote(symbol: str) -> str:
            """Read the current price."""
            return "10"
        model = OpenAIClient("chatgpt-plus/test", provider="openai").get_llm()
        self.assertIsInstance(model, ChatGPTPlanChatModel)
        fake = Mock()
        fake.response.return_value = {"model": "test", "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]}
        with patch("tradingagents.llm_clients.chatgpt_plan.get_chatgpt_plan_service", return_value=fake):
            self.assertEqual(model.bind_tools([quote]).invoke("hello").content, "OK")
            self.assertEqual(asyncio.run(model.ainvoke("hello")).content, "OK")
        body = fake.response.call_args_list[0].args[0]
        self.assertEqual(body["tools"][0]["type"], "namespace")
        self.assertEqual(body["model"], "test")
        self.assertNotIn("api_key", body)

    def test_admin_routes_and_session_errors_do_not_log_out_app(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.routers.chatgpt_plan import router
        from app.routers.auth_db import get_current_user
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: {"is_admin": False}
        client = TestClient(app)
        self.assertEqual(client.get("/chatgpt-plan/status").status_code, 403)
        app.dependency_overrides[get_current_user] = lambda: {"is_admin": True}
        with patch("app.routers.chatgpt_plan.get_chatgpt_plan_service", return_value=self.service):
            self.assertEqual(client.get("/chatgpt-plan/status").status_code, 200)
            result = client.get("/chatgpt-plan/models")
            self.assertEqual(result.status_code, 409)
            self.assertEqual(result.json()["detail"]["code"], "not_connected")


if __name__ == "__main__":
    unittest.main()
