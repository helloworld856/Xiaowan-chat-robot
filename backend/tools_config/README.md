# 工具规格（spec）

`*_spec.py` 中的规格描述模型可以调用什么工具、何时使用它，以及调用时要传哪些参数。工具的实际操作由同目录下的 Python 函数完成。

现有规格见[天气规格](weather_tool/weather_spec.py)、[时间规格](time_tool/time_spec.py)、[早期对话检索规格](history_tool/history_spec.py)、[联网搜索规格](search_tool/search_spec.py)和[网页阅读规格](url_tool/url_spec.py)。[注册表](registry.py)通过 `register_tool` 把规格与实现绑定成 `CHAT_TOOLS`，统一执行参数校验、单工具超时保护、每轮调用次数上限、外部网页内容污染隔离（`taints_context`）以及破坏性写操作两阶段确认（`write_destructive`）。

## 字段说明

| 字段 | 作用 | 当前示例 |
| --- | --- | --- |
| `type` | 声明外层工具的类型。这里使用 `function`，表示通过函数名和参数调用。 | `"function"` |
| `function` | 函数定义对象，包含工具名、用途和参数规格。 | 下方的 `get_weather` 定义 |
| `function.name` | 模型调用时使用的工具名。在同一个代理的工具列表中应唯一。 | `get_weather` |
| `function.description` | 告诉模型工具做什么、在什么场景使用，以及结果的范围。 | 用户询问指定城市的当前天气时，查询气温和相对湿度 |
| `function.parameters` | 描述整组输入参数的 JSON Schema。 | 天气工具的输入规格 |
| `parameters.type` | 指定整组参数的数据类型。`object` 表示按参数名传值。 | `{"city": "杭州"}` |
| `parameters.properties` | 列出工具接受的参数。键名应与 Python 函数的参数名一致。 | `city`、`timezone` |
| `properties.<参数>.type` | 指定单个参数值的数据类型。 | `city` 是 `string`，应传城市名称字符串 |
| `properties.<参数>.description` | 告诉模型这个参数代表什么、该怎样填写。适合写格式、单位和示例。 | `timezone` 使用 IANA 时区名称，例如 `Asia/Shanghai` |
| `properties.<参数>.default` | 向模型说明省略参数时的默认值。实际默认值由 Python 函数提供，两处应保持一致。 | `timezone` 默认 `Asia/Shanghai` |
| `parameters.required` | 列出必须提供的参数名。空数组 `[]` 表示可以省略全部参数。 | 天气工具必须传 `city`，时间工具可以省略 `timezone` |

## 示例：查询天气

天气工具的规格转换成 JSON 后是这样：

```json
{
  "type": "function",
  "function": {
    "name": "get_weather",
    "description": "用户询问指定城市的当前天气时，查询气温和相对湿度。仅支持当前天气，询问天气预报或历史天气时说明无法提供。",
    "parameters": {
      "type": "object",
      "properties": {
        "city": {
          "type": "string",
          "description": "要查询的城市名称，例如杭州。用户没给出可确定的城市时，先询问地点。"
        }
      },
      "required": ["city"]
    }
  }
}
```

用户问「杭州现在多少度？」时，模型可以选择 `get_weather` 并传入 `{"city": "杭州"}`。

天气工具还接受可选参数 `days`（0–7，默认 0）。`0` 只返回当前天气；大于 0 时附带从今天起的每日预报。

| 用途 | 参数示例 |
| --- | --- |
| 现在的天气 | `{"city": "杭州"}` |
| 今天会不会下雨、最高几度 | `{"city": "杭州", "days": 1}` |
| 明天天气 | `{"city": "杭州", "days": 2}` |
| 这周天气 | `{"city": "杭州", "days": 7}` |

当前天气包含天气现象、气温、体感温度、相对湿度、风速和降水量；体感、风速和降水缺失时省略。每日预报包含天气现象、最低/最高气温和降水概率。天气现象由 Open-Meteo 的 WMO 代码转换为中文，见 `WEATHER_CODES`。预报条目少于请求天数时视为服务异常。

## 示例：时间工具

`get_current_time` 使用本机时钟和 IANA 时区数据。日期使用公历，默认时区为 `Asia/Shanghai`。

| 用途 | 参数示例 |
| --- | --- |
| 查询纽约现在几点 | `{"timezone": "America/New_York"}` |
| 计算今天起 30 天后的日期和星期 | `{"operation": "add_days", "days": 30}` |
| 计算距离指定日期还有几天 | `{"operation": "days_between", "end_date": "2027-01-01"}` |
| 将北京时间换算为伦敦时间 | `{"operation": "convert_timezone", "local_datetime": "2026-11-23T20:00", "target_timezone": "Europe/London"}` |

日期间隔按结束日期减起始日期计算，同一天返回 0。转换时遇到夏令时导致的重复时刻时，工具会列出两个候选偏移量供确认。

## 修改规格时一起检查什么

- **描述要符合实现。** 天气工具返回当前天气和最多 7 天的每日预报。新增能力时，再更新对应的描述和参数。
- **参数名、类型和默认值要一致。** 新增必填参数时，同时更新 `properties`、`required` 和 Python 函数签名。时间工具的规格和函数共用 `DEFAULT_TIMEZONE`。
- **用代码检查输入。** 规格和描述用于指导模型。注册表用 `validate_call` 按 Python 函数签名校验实际调用，工具函数还负责业务校验，例如判断时区是否存在、检查城市名称长度。
