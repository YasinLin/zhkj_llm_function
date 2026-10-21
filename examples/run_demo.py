"""示例：外部工具 + 插件声明 + 统一返回结构。

运行（.venv）：
    .venv\\Scripts\\python.exe examples\\run_demo.py

说明：
- 工具在外部定义，调用时通过 tools= 传入；插件内不定义任何工具函数。
- 工具的 parameters 由框架依据函数签名自动反射生成，调用方与插件都不用手写。
- 返回值即声明的出参模型实例（CourierTraceOutput），扩展信息通过代理属性访问。
- 真实模型：设置环境变量 LLM_API_KEY / LLM_MODEL / LLM_BASE_URL 后运行。
"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm  # noqa: E402

from zhkj_llm_function import ToolDef  # noqa: E402
from plugins.courier_plugin import CourierAgentPlugin  # noqa: E402


# --------------------------------------------------------------- 外部工具
async def query_courier_waybill(waybill_no: str, phone_tail: str | None = None) -> dict:
    """查询快递运单当前状态与轨迹节点。"""

    return {
        "waybill_no": waybill_no,
        "carrier": "顺丰速运",
        "status": "已签收",
        "signed": True,
        "delivered_at": "2026-03-05 10:21:00",
        "traces": [
            {"time": "2026-03-04 20:10:00", "context": "【杭州市】快件已到达杭州转运中心", "status": "运输中"},
            {"time": "2026-03-05 08:12:00", "context": "【杭州市】快件正在派送中", "status": "派送中"},
            {"time": "2026-03-05 10:21:00", "context": "【杭州市】快件已签收，签收人：本人", "status": "已签收"},
        ],
    }


async def query_courier_with_phone(waybill_no: str, phone_tail: str) -> dict:
    """带手机号后四位鉴权后重查运单。"""

    return await query_courier_waybill(waybill_no, phone_tail)


COURIER_TOOLS: list[ToolDef] = [
    {
        "name": "query_courier_waybill",
        "description": "查询快递运单当前状态与轨迹节点列表",
        "func": query_courier_waybill,
    },
    {
        "name": "query_courier_with_phone",
        "description": "带寄/收件人手机号后四位鉴权重查运单",
        "func": query_courier_with_phone,
    },
]



# --------------------------------------------------------------- 离线：假模型
def install_fake_llm() -> None:
    """假模型：先要求调用快递工具，再输出符合出参结构的 JSON。"""

    responses = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                type="function",
                                function=SimpleNamespace(
                                    name="query_courier_waybill",
                                    arguments=json.dumps({"waybill_no": "SF1234567890"}),
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "waybill_no": "SF1234567890",
                                "carrier": "顺丰速运",
                                "current_status": "已签收",
                                "is_signed": True,
                                "delivered_at": "2026-03-05 10:21:00",
                                "traces": [
                                    {
                                        "time": "2026-03-05 10:21:00",
                                        "context": "【杭州市】快件已签收，签收人：本人",
                                        "status_desc": "已签收",
                                    }
                                ],
                                "summary": "运单已由顺丰揽收并于 2026-03-05 10:21 签收。",
                                "error_message": "",
                            },
                            ensure_ascii=False,
                        ),
                        tool_calls=[],
                    ),
                    finish_reason="stop",
                )
            ],
            usage=None,
        ),
    ]
    state = {"index": 0}

    async def _acompletion(**kwargs):
        index = min(state["index"], len(responses) - 1)
        state["index"] += 1
        return responses[index]

    litellm.acompletion = _acompletion


async def main() -> None:
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    base_url = os.getenv("LLM_BASE_URL", "https://api.kimi.com/coding/v1")
    if not api_key:
        print("[demo] 未检测到 LLM_API_KEY，使用内置假模型离线演示")
        install_fake_llm()

    plugin = CourierAgentPlugin(
        model=model,
        temperature=1,
        api_key=api_key,
        model_kwargs={"base_url": base_url} if api_key else None,
    )
    result = await plugin.courier_trace_agent(
        {"waybill_no": "SF1234567890", "include_history": True},
        COURIER_TOOLS,
    )
    # 返回值就是声明的出参模型实例；扩展信息通过代理属性暴露，不污染出参结构
    print(
        json.dumps(
            {
                "data": result.model_dump(),
                "ok": result.ok,
                "finish_reason": result.finish_reason,
                "meta": result.meta,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
