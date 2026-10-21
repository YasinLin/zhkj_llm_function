"""示例插件：结合「明天」的天气，规划明天的行程。

遵循框架规范：
- 继承 BaseAgentGroup，agent 能力用 @agent_def 声明
- 入参/出参为 Pydantic BaseModel
- 工具由外部调用时传入，插件内不定义工具函数
- 方法体只写 ...，业务指令写在 docstring 内
"""

from pydantic import BaseModel, Field

from zhkj_llm_function import BaseAgentGroup, ToolDef, agent_def


class WeatherPlanInput(BaseModel):
    """天气行程规划入参"""

    city: str = Field(..., description="城市名称，例如 杭州")
    date: str | None = Field(
        default=None, description="规划日期，格式 YYYY-MM-DD；不传表示规划明天"
    )
    activity_types: list[str] = Field(
        default_factory=lambda: ["outdoor", "indoor"],
        description="偏好的活动类型，可选 outdoor(户外) / indoor(室内) / shopping / sports",
    )
    people_count: int = Field(default=1, description="出行人数")
    avoid_crowded: bool = Field(default=False, description="是否需要避开人流高峰")


class WeatherPlanItem(BaseModel):
    """行程安排条目"""

    time_range: str = Field(..., description="时间段，例如 09:00-12:00")
    title: str = Field(..., description="活动名称")
    activity_type: str = Field(..., description="活动类型：outdoor/indoor/shopping/sports/rest")
    weather_basis: str = Field(..., description="天气依据，例如 降水概率 20%、气温 24℃")
    reason: str = Field(..., description="安排理由")


class WeatherPlanOutput(BaseModel):
    """天气行程规划出参"""

    plan_date: str = Field(..., description="规划日期，格式 YYYY-MM-DD")
    city: str = Field(..., description="规划城市")
    weather_summary: str = Field(..., description="当日天气概述，例如 多云转小雨")
    temperature_range: str = Field(..., description="当日气温区间，例如 18~26℃")
    precipitation_probability_max: float = Field(..., description="当日最大降水概率，0~1 之间")
    has_heavy_rain: bool = Field(..., description="当日是否存在强降水（暴雨/雷阵雨/大雨）")
    suggestions: list[str] = Field(default_factory=list, description="整体建议列表")
    plan: list[WeatherPlanItem] = Field(default_factory=list, description="按时间顺序的行程安排")
    error_message: str = Field(..., description="查询失败或信息不足时的原因说明，成功时为空字符串")


class WeatherPlanPlugin(BaseAgentGroup):
    """结合明日天气做行程规划的插件。"""

    @agent_def(ttl=86400, compact_config=None)
    async def weather_plan_agent(
        self, payload: WeatherPlanInput, tools: list[ToolDef] | None = None
    ) -> WeatherPlanOutput:
        """
        角色：天气行程规划助手，负责查询目标日期（默认明天）的天气，并据此给出可执行的一天行程安排。

        业务规则：
        1. 必须调用工具查询天气，禁止凭记忆、常识或推测输出任何温度、降水概率、天气状况；信息不足时优先调用工具，禁止编造。
        2. 工具调用策略：
           1) 优先使用 query_weather_forecast 查询城市在目标日期的天气；
           2) 若返回结果中缺少目标日期的数据，只能再调用一次该工具核对日期，禁止反复重试或改用其他工具臆造数据；
           3) 一次只调用一个工具，按上述顺序调用，禁止并行猜测。
        3. 日期规则：
           1) plan_date 取 payload.date；payload.date 为空时，取「当前日期的次日」，当前日期已在用户输入中给出；
           2) 查询工具必须使用与 plan_date 完全一致的日期字符串，不得错用今天或其他日期。
        4. 天气归一化：
           1) weather_summary 用中文描述当日天气（如 晴/多云/小雨/雷阵雨/雪）；
           2) temperature_range 输出「最低~最高℃」格式，例如 18~26℃；
           3) precipitation_probability_max 取当日最大降水概率，取值 0~1 的小数，保留两位；
           4) has_heavy_rain 为 true 的条件：降水概率 ≥ 0.6 且天气为大雨/暴雨/雷阵雨，或天气码属于强降水类别。
        5. 行程安排规则：
           1) 按上午、下午、晚上三个时间段至少给出 3 条行程，时间段要连续且不重叠；
           2) 降水概率 ≥ 0.5 或有雷暴/雨雪时，该时段的活动类型必须为 indoor/shopping/rest，禁止安排 outdoor/sports；
           3) 最高气温 ≥ 33℃ 或最低气温 ≤ 5℃ 时，减少 outdoor 时段，优先安排 indoor/rest；
           4) 降水概率 ≤ 0.2 且气温在 15~28℃ 时，优先安排 payload.activity_types 中的户外类活动；
           5) 每条行程必须写明 weather_basis（引用具体天气数值）与 reason（为什么这样安排）。
        6. suggestions 给出 2~4 条整体建议，例如带伞、注意高温、适合户外等，必须与当日天气数据一致。
        7. 若 tools 中不存在天气查询工具，则不做任何查询，plan_date 与 city 回显，其余字段置为未知/空值，并在 error_message 中写明「未提供天气查询工具，无法查询」。
        8. 工具调用失败、超时或返回非法数据时，保留 plan_date 与 city 回显，其余字段置为未知/空值，并把失败原因原文写入 error_message，禁止用猜测值填充。
        9. 最终输出必须严格符合 WeatherPlanOutput 结构：仅输出结构化字段，禁止附加任何额外自然语言、解释、Markdown 或致歉语。
        """
        ...
