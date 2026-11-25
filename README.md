# Subgraph Partitioning · 科研子图切分工作台

Java 21 业务后端、Python LangGraph / LangChain ReAct Agent、React 标注界面。用户自行配置支持视觉与原生工具调用的 Chat Completions 或 Responses 接口。

完整需求与里程碑见 [开发计划](docs/开发计划.md)。源码、运行产物与原始资料隔离；仓库不包含原图、旧切分包、API Key、运行数据库或模型权重。

## 本机启动

需要 64 位 Python 3.12、Java 21、Maven 3.9、Node.js 22.12 或更新的兼容版本。在 PowerShell 进入工程目录：

```powershell
.\setup.ps1
.\start.ps1
```

启动后访问 `http://127.0.0.1:8765`。停止使用 `.\stop.ps1`。脚本只操作此工程记录且命令行、启动时间都匹配的进程；端口冲突会报错，不会结束其他程序。

首次 OCR 使用需要下载 PaddleOCR 权重。有网络或运行库问题时会记录 OCR 不可用，并阻止自动通过；仍可手工标注、确认。只安装基础界面与 Agent 依赖可使用 `setup.ps1 -SkipOcr`。

Windows OCR 默认使用 CPU、4 个线程，并关闭 MKL-DNN / oneDNN。模型缓存默认在 `%LOCALAPPDATA%\SubgraphPartitioning\models`，避免中文工程路径影响原生模型加载；图片仍可位于中文路径。若用户目录也含中文，会尝试 Windows 短路径；短路径不可用时，请在启动前设置一个有写权限的纯 ASCII 路径，例如 `$env:SFA_OCR_CACHE = 'D:\SubfigureModels'`。已有 `PADDLE_PDX_CACHE_HOME`、模型来源设置会被保留，`SFA_OCR_CACHE` 优先。调整后重启服务生效，详细错误见 `runtime/agent.err.log`。

## 使用流程

1. 在“模型配置”中填写接口地址和 API Key，点击“获取模型列表”，搜索并选择模型；也可以手动填写模型 ID。选择协议与思考程度，保存后点击“测试能力”。测试会发送系统生成的数字图片，检查视觉、工具调用和工具结果回传，产生少量 API 调用。
2. 上传 PNG / JPEG 原图；一次批次最多 500 张，界面分组上传。
3. 选择已验证模型，启动批次。默认并发 2 张，每图 24 次模型请求、3 轮返修、10 分钟预算。
4. 对待复核图片调整矩形与标签。“保存并试切”生成新草稿，并运行 OCR 和文字完整性检查；“人工确认”表示已经检查内容与警告。已有版本保留原记录，保存新草稿后按当前规则重新检查。
5. 人工确认后可“存为人工标准答案”，用于后续质量评测。
6. 正式 ZIP 只包含自动通过或人工确认版本；“含草稿”会将未通过内容放入单独的 `draft/` 目录。`manifest.json` 始终列出未解决项。

本机原资料位于工程父目录时，“旧结果作草稿”可读取 `子图划分结果/result12`，映射旧绝对路径并核对文件名和尺寸。旧结果不是标准答案。

模型列表通过服务器请求兼容接口的 `GET /models` 获取，支持基础 URL 或以 `/chat/completions`、`/responses` 结尾的地址。列表不等于视觉能力认证。编辑已有接口时，API Key 留空可复用已保存密钥；若改变地址，获取列表前需重新填写密钥。获取列表不会自动保存配置，列表不可用时仍能手动填模型 ID。

思考程度可选接口默认、none、minimal、low、medium、high、xhigh、max。默认不发送额外参数；Chat Completions 使用 `reasoning_effort`，Responses 使用 `reasoning.effort`。这些选项由工作台透传，具体模型支持哪些档位以供应商为准，不支持时明确报错，不自动降级。修改配置后必须重新测试能力；已有任务保留启动时的供应商配置快照。

## 原图、坐标与保存

- 上传副本保存在 `runtime/images/<id>/original/`，方向规范化副本单独保存为 PNG。
- 所有坐标为规范化原图像素 `[x1,y1,x2,y2)`；局部与缩略图观察返回原图映射。
- 以子图主体及其内部文字、坐标轴、刻度、图例、比例尺的完整性为准。外部分组标题（如细胞类型标题）可以省略，也可以部分带入；默认 Agent 不再为它们创建独立裁图。用户手动保留的共享标题仍可输出独立图片与 `panel_ids` 关联。
- `TEXT_INTERSECTION` 检查实际字形笔画是否在所属面板中被截断，不再仅依据 OCR 矩形相交。OCR 白边或少量抗锯齿灰边越界、文字在所属面板完整保留但部分带入邻图、已识别的外部分组标题不会触发文字警告。保留 OCR 多边形，以减少斜排文字包围框带来的误判。
- 文字归属和外部标题的自动识别使用标签位置、独立标题带和像素证据，仍可能误判。独立视觉复核按同一完整性规则检查，内部图题不能因字符串与分组标题相同而豁免；图形截断仍需视觉检查。没有原图像素或 OCR 失败时不能自动通过。
- 单纯裁框交叠显示为参考信息，不再单独阻止自动通过。实际截断或影响阅读的重叠内容仍由文字检查与视觉复核拦截。
- 人工编辑要求匹配版本，运行中的图片不可被覆盖。
- Java 使用 `runtime/app.sqlite`；Python 使用独立的 `agent-checkpoints.sqlite`，保存真实 LangGraph 状态与人工暂停点。
- API Key 由 Windows DPAPI 加密；工作流检查点不保存密钥。两个服务仅监听 `127.0.0.1`，内部接口需要每次启动生成的随机令牌。
- 运行日志在 `runtime/`。批次恢复复用检查点；对无法确认是否已被远端执行的在途请求，恢复时可能再次计费。

## 开发与验证

```powershell
$env:PYTHONPATH = 'agent'
.\.venv\Scripts\python.exe -m pytest agent/tests -q
mvn -f backend/pom.xml test
.\.venv\Scripts\python.exe tools/smoke_http.py
# 本地父目录有 图片/15.png 时，额外验证真实 OCR 的 Java→Python 调用链：
.\.venv\Scripts\python.exe tools/smoke_http.py --ocr
# 本地15/a2原图与已保存45布局的真实字形回归，不调用视觉模型：
.\.venv\Scripts\python.exe tools/check_text_policy.py
cd frontend
npm.cmd run build
```

几何回归工具：

```powershell
.\.venv\Scripts\python.exe tools/evaluate.py --gold gold.json --pred exported-batch.zip --output evaluation.json
```

该工具仅报告标签与框 IoU；文字、图形完整性和自动通过正确率仍需要人工标准答案审核，不能由 IoU 替代。

## 验证边界

工程功能测试与真实视觉模型质量验收分别记录于 `docs/验证记录.md`。没有用户配置的模型、46 张已确认标准答案和独立新图时，不能声称已达到计划中的高精度验收门槛。
