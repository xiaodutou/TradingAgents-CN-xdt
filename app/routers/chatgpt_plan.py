"""Admin-only controls for a personal ChatGPT plan connection."""
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.routers.auth_db import get_current_user
from app.services.chatgpt_plan_service import MODEL_PREFIX, ChatGPTPlanError, get_chatgpt_plan_service

router = APIRouter(prefix="/chatgpt-plan", tags=["chatgpt-plan"])


def admin(user=Depends(get_current_user)):
    if not user.get("is_admin"):
        raise HTTPException(403, "仅管理员可管理 ChatGPT 会员连接")
    return user


def invoke(method, *args):
    try:
        return {"success": True, "data": method(*args), "message": ""}
    except ChatGPTPlanError as exc:
        raise HTTPException(409 if exc.status == 401 else exc.status, {"message": str(exc), "code": exc.code}) from None


class AccountRequest(BaseModel):
    profile_id: str | None = None


class ModelRequest(BaseModel):
    slug: str


@router.get("/status")
def status(user=Depends(admin)):
    return invoke(get_chatgpt_plan_service().status)


@router.post("/sign-in")
def sign_in(body: AccountRequest, user=Depends(admin)):
    return invoke(get_chatgpt_plan_service().begin, body.profile_id)


@router.post("/select")
def select(body: AccountRequest, user=Depends(admin)):
    return invoke(get_chatgpt_plan_service().select, body.profile_id)


@router.post("/sign-out")
def sign_out(body: AccountRequest, user=Depends(admin)):
    return invoke(get_chatgpt_plan_service().sign_out, body.profile_id)


@router.get("/models")
def models(user=Depends(admin)):
    return invoke(get_chatgpt_plan_service().models)


@router.post("/activate-model")
async def activate_model(body: ModelRequest, user=Depends(admin)):
    service = get_chatgpt_plan_service()
    try:
        catalog = await asyncio.to_thread(service.models)
    except ChatGPTPlanError as exc:
        raise HTTPException(409 if exc.status == 401 else exc.status, str(exc)) from None
    model = next((m for m in catalog if m["slug"] == body.slug), None)
    if not model:
        raise HTTPException(400, "请选择当前账号模型目录中的模型")
    from app.services.config_service import config_service
    from app.models.config import LLMConfig
    config = await config_service.get_system_config()
    if config is None:
        raise HTTPException(500, "无法读取系统配置")
    name = MODEL_PREFIX + body.slug
    # Do not replace the user's ordinary API/provider settings.
    config.llm_configs = [m for m in config.llm_configs if m.model_name != name]
    config.llm_configs.insert(0, LLMConfig(
        provider="openai", model_name=name, model_display_name="ChatGPT Plus · " + model["display_name"],
        api_key="chatgpt-plan-oauth", api_base="https://api.openai.com/v1", enabled=True,
        description="使用已授权的 ChatGPT 会员额度；凭证仅保存在本地后端。",
        capability_level=3, suitable_roles=["both"], features=["tool_calling"],
        recommended_depths=["快速", "基础", "标准"], timeout=300,
    ))
    config.default_llm = name
    config.system_settings["quick_analysis_model"] = name
    config.system_settings["deep_analysis_model"] = name
    if not await config_service.save_system_config(config):
        raise HTTPException(500, "模型配置保存失败")
    return {"success": True, "data": {"model_name": name}, "message": "已添加会员模型并设为默认，请刷新分析页。"}


@router.post("/test")
def test_model(body: ModelRequest, user=Depends(admin)):
    from tradingagents.llm_clients.chatgpt_plan import ChatGPTPlanChatModel
    from langchain_core.messages import HumanMessage
    try:
        catalog = get_chatgpt_plan_service().models()
        if body.slug not in {m["slug"] for m in catalog}:
            raise HTTPException(400, "模型不在当前账号目录中")
        message = ChatGPTPlanChatModel(model_name=MODEL_PREFIX + body.slug).invoke([HumanMessage("请只回复：连接成功")])
        return {"success": True, "data": {"text": message.content, "usage": message.usage_metadata},
                "message": "测试完成，本次使用了 ChatGPT 会员额度。"}
    except ChatGPTPlanError as exc:
        raise HTTPException(409 if exc.status == 401 else exc.status, {"message": str(exc), "code": exc.code}) from None
