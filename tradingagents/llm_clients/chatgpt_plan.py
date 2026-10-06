"""LangChain adapter for official ChatGPT plan usage via streamed Responses."""
import asyncio
import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from app.services.chatgpt_plan_service import MODEL_PREFIX, ChatGPTPlanError, get_chatgpt_plan_service


def response_input(messages):
    items = []
    for message in messages:
        if isinstance(message, ToolMessage):
            items.append({"type": "function_call_output", "call_id": message.tool_call_id,
                          "output": message.content if isinstance(message.content, str) else json.dumps(message.content, ensure_ascii=False)})
        elif isinstance(message, AIMessage) and message.additional_kwargs.get("chatgpt_plan_output"):
            # Keep reasoning items and namespace/call IDs for stateless tool continuation.
            items.extend(message.additional_kwargs["chatgpt_plan_output"])
        else:
            role = "developer" if isinstance(message, SystemMessage) else "assistant" if isinstance(message, AIMessage) else "user"
            content = message.content
            if not isinstance(content, str):
                raise ChatGPTPlanError("当前分析流程仅支持文本消息。")
            if content:
                items.append({"role": role, "content": content})
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    items.append({"type": "function_call", "call_id": call["id"], "name": call["name"],
                                  "namespace": "tradingagents", "arguments": json.dumps(call["args"], ensure_ascii=False)})
    return items


def response_message(result):
    output = result.get("output", [])
    text, calls = [], []
    for item in output:
        if item.get("type") == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    text.append(block.get("text", ""))
                elif block.get("type") == "refusal":
                    text.append(block.get("refusal", ""))
        elif item.get("type") == "function_call":
            try:
                arguments = json.loads(item.get("arguments", "{}"))
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments")
            except ValueError:
                raise ChatGPTPlanError("ChatGPT 返回的工具参数无效。", "invalid_tool_call", 502) from None
            calls.append({"id": item["call_id"], "name": item["name"].removeprefix("tradingagents."),
                          "args": arguments, "type": "tool_call"})
    if not text and not calls:
        raise ChatGPTPlanError("ChatGPT 未返回文本或工具调用。", "empty_response", 502)
    usage = result.get("usage") or {}
    return AIMessage(content="".join(text), tool_calls=calls,
                     additional_kwargs={"chatgpt_plan_output": output},
                     response_metadata={"model_name": MODEL_PREFIX + result.get("model", ""), "billing_source": "chatgpt_plan"},
                     usage_metadata={"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
                                     "total_tokens": usage.get("total_tokens", 0)})


class ChatGPTPlanChatModel(BaseChatModel):
    model_name: str
    temperature: float = 0.0  # Compatibility attribute only; never sent to this route.
    max_tokens: int | None = None
    timeout: int = 180

    @property
    def _llm_type(self):
        return "chatgpt-plan-responses"

    @property
    def _identifying_params(self):
        return {"model_name": self.model_name, "billing_source": "chatgpt_plan"}

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        converted = []
        for tool in tools:
            function = convert_to_openai_tool(tool)["function"]
            converted.append({"type": "function", "name": function["name"],
                              "description": function.get("description", ""),
                              "parameters": function.get("parameters", {"type": "object", "properties": {}}), "strict": False})
        if tool_choice not in (None, "auto", "none", "required"):
            raise ChatGPTPlanError("ChatGPT 会员接口暂不支持指定单个工具。")
        return self.bind(tools=[{"type": "namespace", "name": "tradingagents", "description": "Stock research data tools", "tools": converted}],
                         **({"tool_choice": tool_choice} if tool_choice else {}))

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if stop:
            raise ChatGPTPlanError("ChatGPT 会员接口不支持 stop 参数。")
        payload = {"model": self.model_name.removeprefix(MODEL_PREFIX), "input": response_input(messages),
                   "include": ["reasoning.encrypted_content"]}
        if kwargs.get("tools"):
            payload["tools"] = kwargs["tools"]
        if kwargs.get("tool_choice"):
            payload["tool_choice"] = kwargs["tool_choice"]
        result = get_chatgpt_plan_service().response(payload, timeout=self.timeout)
        return ChatResult(generations=[ChatGeneration(message=response_message(result))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return await asyncio.to_thread(self._generate, messages, stop, None, **kwargs)
