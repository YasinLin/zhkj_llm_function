"""示例插件：快递物流轨迹查询 agent。

遵循框架规范：
- 继承 BaseAgentGroup
- 每一个 agent 能力是一个 async 方法，用 @agent_def(ttl=..., compact_config=None) 装饰
- 方法签名固定：async def xxx_agent(self, payload: XXXInput, tools: list[ToolDef] | None = None) -> XXXOutput
- 入参/出参单独定义 Pydantic BaseModel
- 工具由外部调用时传入，插件内不定义工具函数
- 方法体只写 ...，业务指令写在 docstring 内
"""

from pydantic import BaseModel, Field

from zhkj_llm_function import BaseAgentGroup, ToolDef, agent_def


class CourierTraceInput(BaseModel):
    """快递轨迹查询入参"""

    waybill_no: str = Field(..., description="快递运单号/快递单号")
    phone_tail: str | None = Field(
        default=None, description="寄/收件人手机号后四位，部分快递公司查询鉴权需要"
    )
    include_history: bool = Field(
        default=True, description="是否需要近30天历史轨迹，False 时仅查当前节点"
    )


class CourierTraceItem(BaseModel):
    """单条轨迹节点"""

    time: str = Field(..., description="节点发生时间，格式 YYYY-MM-DD HH:MM:SS")
    context: str = Field(..., description="节点描述原文")
    status_desc: str = Field(..., description="归一化节点状态，如运输中/已签收/派送中/异常")


class CourierTraceOutput(BaseModel):
    """快递轨迹查询出参"""

    waybill_no: str = Field(..., description="回显的运单号")
    carrier: str = Field(..., description="承运快递公司名称，未知时为空字符串")
    current_status: str = Field(..., description="当前运单状态，如 已签收/运输中/待揽收")
    is_signed: bool = Field(..., description="是否已签收")
    delivered_at: str = Field(..., description="签收时间，未签收为空字符串")
    traces: list[CourierTraceItem] = Field(
        default_factory=list, description="轨迹节点列表，按时间升序"
    )
    summary: str = Field(..., description="一句话中文结论摘要")
    error_message: str = Field(..., description="查询失败或信息不足时的原因说明，成功时为空字符串")


class CourierAgentPlugin(BaseAgentGroup):
    """快递物流轨迹查询插件。"""

    @agent_def(ttl=86400, compact_config=None)
    async def courier_trace_agent(
        self, payload: CourierTraceInput, tools: list[ToolDef] | None = None
    ) -> CourierTraceOutput:
        """
        角色：快递物流轨迹查询助手，负责根据用户提供的运单号查询快递公司的最新物流状态与历史轨迹，并给出可读结论。

        业务规则：
        1. 必须调用工具查询，禁止凭记忆或推测输出任何物流状态、时效、签收结果；信息不足时优先调用工具，禁止编造。
        2. 工具调用策略：
           1) 优先使用 query_courier_waybill 查询运单当前状态与轨迹列表；
           2) 若该工具返回“需要手机号后四位鉴权”或“运单号不存在/无权限”，且本次传入 phone_tail，则调用 query_courier_with_phone 携带后四位重查；
           3) 若 payload.include_history 为 True 且工具返回的轨迹节点数明显少于近30天预期，可再调用 query_courier_history 补齐历史轨迹；
           4) 一次只调用一个工具，按上述顺序依次调用，禁止并行猜测多个工具。
        3. 若 tools 中不存在上述任一工具，则不做任何查询，直接输出 waybill_no 回显、current_status="未知"、is_signed=false、traces 为空，并在 error_message 中写明“未提供快递查询工具，无法查询”。
        4. 工具调用失败、超时或返回非法数据时，保留运单号回显，其余字段置为未知/空值，并把失败原因原文写入 error_message，禁止用猜测值填充。
        5. 轨迹归一化：把工具返回的原始节点按 time 升序排序，status_desc 归一化为“待揽收/已揽收/运输中/派送中/已签收/退回/异常”之一；无法判定时填“异常”并在 summary 中说明。
        6. is_signed 仅当存在签收节点或工具明确返回已签收状态时为 true，否则为 false；delivered_at 取签收节点时间，未签收为空字符串。
        7. summary 用一句中文概括当前状态与关键节点（如“运单已由顺丰揽收，当前运输中，最新节点为 2026-03-05 08:12 已到达杭州转运中心”），不超过60 字。
        8. 最终输出必须严格符合 CourierTraceOutput 结构：仅输出结构化字段，禁止附加任何额外自然语言、解释、Markdown 或致歉语。
        """
        ...
