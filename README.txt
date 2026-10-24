============================================================
  华为VRP基线检查工具 v2.2
  离线绿色版 (自带Python 3.12 ARM64 Linux运行时)
============================================================

一、概述
------------------------------------------------------------

本工具用于对华为S系列/CE系列园区交换机进行基线配置合规
检查。通过Telnet登录设备抓取 display current-configuration
输出, 依据规则库自动判定配置是否符合基线要求, 并生成
Markdown整改文档和Excel汇总报告。

支持在线(Telnet实时采集)和离线(读取快照)两种工作模式。
离线模式下无需连接设备, 适合事后审计和规则调试。

v2.2 新增 Web 前端, 支持浏览器操作全部功能(设备管理/
规则开关/启动检查/实时进度/报告查看/文件下载)。

★ 自带 ARM64 Linux Python 3.12 运行时, 目标机器无需安装
  任何Python环境或第三方依赖, 解压即用。

★ 2026-10-21 完成一次全量代码审计, 修复 18 项缺陷(详见
  docs/AUDIT-2026-10-21.md)。回归测试:
    python tests/test_regression.py

★ 每次 push / PR 会自动执行 CI(.github/workflows/ci.yml):
  回归测试(Python 3.12 与 3.13) + 凭据卫生检查 + 容器构建与部署链路。


二、文件清单
------------------------------------------------------------

  文件/目录            说明
  ─────────────────────────────────────────────────────────
  cli.py              CLI入口(argparse, 调用core/)
  vrp_baseline.py     向后兼容wrapper(透传cli.py)
  core/               核心引擎(7个模块, 零Web依赖)
    models.py           数据结构(Rule/Section/Check/Finding)
    rule_loader.py      规则库加载 + 字段校验 + 变量校验
    config_parser.py    VRP配置解析(分段/模板编译/选择器)
    builtin_checks.py   内置检查(策略链追踪/VLAN1禁用)
    engine.py           规则执行引擎(run_rule/run_all)
    collector.py        Telnet采集 + 离线快照(telnetlib自适应)
    credentials.py      凭证安全(环境变量 + CSV加载)
    reporter.py         报告生成(结构化数据 + MD/XLSX)
  api/                FastAPI接口层(复用core/)
    app.py               FastAPI实例 + CORS + SSE + 静态文件
    schemas.py           Pydantic模型
    store.py             线程安全存储(设备/任务/结果)
    task_manager.py      异步任务管理
    routes/              路由(devices/profile/tasks/system)
  web/                前端静态资源(单页应用)
    index.html           页面
    style.css            样式
    app.js               交互逻辑(REST + SSE)
  rules.yaml          规则库(由程序作者维护, 用户不应修改)
  profile.yaml        用户配置(规则开关 + 变量值, 唯一需编辑)
  devices.csv.example 设备清单模板(复制为 devices.csv 后填写)
  devices.csv         设备清单(本地文件, 不入库, 勿提交明文口令)
                       Web端增删改会写回本文件(不写入口令), 重启后生效;
                       挂载为只读时自动降级为仅内存生效。
  snapshots/          配置快照目录(在线自动保存, 离线读取; 文件名为 <设备IP>.cfg)
  docs/               文档目录
    audit/            多轮代码审计报告与复核评分(评分见 AUDIT-REVIEW-2026-10-24.md)
  tests/
    test_regression.py 回归测试(对应审计编号 A01~A18)
    test_engine_semantics.py / test_api_e2e.py  pytest 用例(引擎语义 + API 端到端)
  .github/workflows/  CI流水线
    ci.yml              回归测试(Python 3.12/3.13) + 凭据卫生 + 容器构建
  runtime/            独立Python 3.12 aarch64 Linux运行时
    bin/python3.12       Python解释器(aarch64 ELF二进制)
    lib/python3.12/      标准库(含telnetlib, 3.12自带)
    lib/libpython3.12.so 共享库
  vendor/             全部第三方依赖(无需pip install)
                      纯Python: pyyaml/openpyxl/fastapi/uvicorn/...
                      C扩展: pydantic-core (aarch64 Linux cp312)
  run.sh              CLI启动脚本(使用runtime Python)
  run_web.sh          Web服务启动脚本(使用runtime Python)
  pack.sh             打包脚本(生成tar.gz)
  ─────────────────────────────────────────────────────────


三、快速开始
------------------------------------------------------------

1. 解压到任意目录(建议 ~/vrp-baseline/)

2. 赋予执行权限:
     chmod +x run.sh run_web.sh runtime/bin/python3.12

3. 编辑 devices.csv 填入设备信息:
     type,name,ip,platform,protocol,username,password
     交换机,SW-01,10.10.1.11,s-series,telnet,admin,你的密码

     ★ 凭证安全(推荐): password列留空, 通过环境变量传递密码
       环境变量命名: VRP_PASS_<IP> (IP中点号替换为下划线)
       示例: export VRP_PASS_10_10_1_11=你的密码
       CSV中password为空时, 程序自动从环境变量读取

4. 编辑 profile.yaml:
     a) variables: 填入NTP服务器、SNMP团体字等实际值
     b) rules: 将要检查的规则设为 true, 不需要的设为 false

5. 运行(CLI模式):

   # 环境自检(首次必做):
   ./run.sh --selfcheck

   # 离线自检(用样例配置验证全流程, 不连设备):
   ./run.sh --offline

   # 正式全量检查(Telnet登录所有设备):
   ./run.sh

6. 运行(Web模式):

   # 启动Web服务:
   ./run_web.sh

   # 浏览器访问:
   http://localhost:8080          前端页面
   http://localhost:8080/docs     API文档(Swagger UI)


四、打包与部署
------------------------------------------------------------

★ 本工具自带 ARM64 Linux Python 3.12 运行时, 目标机器
  无需安装任何Python或依赖, 解压即可运行。

★ vendor/ 中的 pydantic-core C扩展已适配 aarch64 Linux
  (cp312-cp312-manylinux_2_17_aarch64), 无需重新编译。

打包(在开发机上):
  ./pack.sh                 打包为 tar.gz (含runtime)
  ./pack.sh --no-runtime    不含Python runtime (约3.4MB)

部署(在目标ARM64 Linux机器上):
  1. 拷贝 tar.gz 到目标机器
  2. tar xzf vrp-baseline-v2.2-*.tar.gz
  3. cd vrp-baseline
  4. chmod +x run.sh run_web.sh runtime/bin/python3.12
  5. ./run.sh --selfcheck           # 环境自检
  6. ./run.sh --offline              # 离线检查
  7. ./run.sh                        # 在线检查(Telnet)
  8. ./run_web.sh                    # 启动Web服务(端口8080)

vendor/ 已包含的依赖清单:
  ─────────────────────────────────────────
  pyyaml              YAML解析(纯Python)
  openpyxl            Excel报告生成(纯Python)
  et_xmlfile          openpyxl依赖(纯Python)
  fastapi             Web框架(纯Python)
  uvicorn             ASGI服务器(纯Python)
  pydantic            数据校验(纯Python)
  pydantic-core       pydantic核心(C扩展, aarch64 Linux)
  starlette           ASGI框架(纯Python)
  anyio               异步IO(纯Python)
  click               CLI(uvicorn依赖, 纯Python)
  h11                 HTTP/1.1(uvicorn依赖, 纯Python)
  idna                域名编码(anyio依赖, 纯Python)
  annotated-types     pydantic依赖(纯Python)
  annotated-doc       pydantic依赖(纯Python)
  typing-extensions   类型扩展(纯Python)
  typing-inspection   类型检查(纯Python)
  python-multipart    文件上传(纯Python)
  opentelemetry-api   可观测性(fastapi依赖, 纯Python)
  ─────────────────────────────────────────


五、设备清单格式 (devices.csv)
------------------------------------------------------------

CSV表头(第一行, 不可省略):
  type,name,ip,platform,protocol,username,password

字段说明:
  字段       必填  说明
  ─────────────────────────────────────────────
  type       是    设备类型, 必须填 "交换机"
  name       是    设备名称(如 "接入SW-01")
  ip         是    设备管理IP
  platform   是    设备平台: s-series 或 ce-series
  protocol   是    采集协议: 目前仅支持 telnet
  username   是    Telnet登录用户名(密码认证模式可留空)
  password   否    Telnet登录密码(为空时从环境变量VRP_PASS_<IP>读取)
  ─────────────────────────────────────────────

设备平台标识:
  s-series  — S系列交换机(S5720/S5735/S6720等)
  ce-series — CE系列云交换机(CE6800/CE12800等, 需commit)


六、用户配置 (profile.yaml)
------------------------------------------------------------

profile.yaml 是用户唯一需要编辑的配置文件, 包含两部分:

1. variables — 全局变量定义
   规则库中通过 ${varName} 引用的变量必须在此定义。

2. rules — 规则开关
   每条规则通过规则ID(true/false)控制是否启用。


七、规则库 (rules.yaml)
------------------------------------------------------------

规则库由程序作者随版本发布维护, 用户不应直接编辑。

规则语法说明:
  kind: vrp      每行都必须在目标配置块内命中(AND逻辑)
  kind: any      至少一行命中即合规(OR逻辑)
  kind: not      每行都不得出现(禁止类检查)
  match: exact   整行精确匹配(忽略首尾空格和缩进)
  match: regex   正则search模式
  builtin        调用引擎内置函数(策略链追踪/VLAN1等复杂逻辑)
  ${var}         引用profile.yaml中定义的变量
  ${if}          内置变量: 当前不合规目标名(接口/视图名)
  ${platform}    内置变量: 当前设备平台

VRP唯一前缀兼容:
  华为VRP支持唯一前缀替代, 如 ntp-service 可缩写为 ntp。
  规则正则使用 ntp(?:-service)? 同时匹配完整和缩写形式。


八、内置检查函数
------------------------------------------------------------

  ipv6_deny_chain  — IPv6流量策略阻断(ACL 0x86dd 五段链追踪)
  dhcp_deny_chain  — DHCP流量策略阻断(UDP 67/68 双向检查)
  vlan1_check       — VLAN1禁用(检查Vlanif1/trunk放行/access划入)

五段链追踪逻辑:
  ACL(deny规则) → 流分类(if-match acl) → 流行为(deny)
  → 流策略(classifier+behavior绑定) → 应用位置(全局/接口)


九、Telnet登录流程
------------------------------------------------------------

程序自动适配两种VRP认证模式:

1. AAA模式 (用户名 + 密码):
   - 等待以 ':' 结尾的提示符(banner不含冒号, 不会误匹配)
   - 先输入用户名, 再等待密码提示
   - 输入密码, 等待VRP提示符 <设备名>

2. 密码认证模式 (仅密码):
   - 首个 ':' 提示符的回显含 "password" 字样
   - 直接输入密码, 等待VRP提示符

3. AAA回退检测:
   - 若输入用户名后未收到密码提示, 回车重试最多3次

设备名约束:
  - VRP提示符正则: <[A-Za-z_]+> (仅字母和下划线)
  - 用户需确保设备sysname只含letter和下划线
  - banner不含冒号 ':' 以区分登录提示符

★ runtime 自带 Python 3.12 (含 telnetlib), 在线模式无需
  额外安装任何包。若使用系统Python >= 3.13 则仅离线模式
  可用(telnetlib已从标准库移除)。


十、输出文件
------------------------------------------------------------

所有报告统一输出到 output/ 目录 (与 docker-compose 的
./data/output:/app/output 挂载点一致):

  output/remediation_report.md  — 整改文档(Markdown格式, 按设备分组)
  output/baseline_report.xlsx   — Excel汇总表
                                  色标: 红=high 黄=medium 绿=low 蓝=suggest
  output/reports/<task_id>/     — Web 端每个任务的报告(按 task_id 隔离,
                                  下载接口按任务号取对应文件)
  snapshots/<IP>.cfg            — 在线模式自动保存的设备配置快照

十之一、Web 服务环境变量
------------------------------------------------------------

  VRP_API_TOKEN        设置后, 所有 /api 请求须带 X-API-Token 头;
                      前端可在"系统信息"页填写令牌(存 localStorage)。
                      不设置则不鉴权, 仅建议在受控内网使用。
  VRP_ALLOWED_ORIGINS  跨域白名单(逗号分隔)。前端页面由本服务同源提供,
                      默认不需要跨域; 仅当把前端单独部署到其它域名时才设置。

十之二、设备清单
------------------------------------------------------------

  devices.csv          本地设备清单, 已加入 .gitignore, 请勿提交明文口令。
                       首次使用: cp devices.csv.example devices.csv
  devices.csv.example  随包发布的模板(口令列为空)

  口令优先用环境变量传递, 不写进 CSV:
     export VRP_PASS_10_10_1_11='你的口令'


十一、规则ID速查
------------------------------------------------------------

  规则ID          严重度   平台      说明
  ─────────────────────────────────────────────────
  SW-NTP-01       high     S/CE     NTP时间同步(兼容ntp/ntp-service)
  SW-SNMP-01      high     S/CE     SNMP只读团体字(RO)
  SW-SNMP-02      high     S/CE     SNMP读写团体字(RW)
  SW-SNMP-03      high     S/CE     SNMP v2c版本
  SW-SNMP-04      suggest  S/CE     SNMP Trap告警主机
  SW-SNMP-05      suggest  S/CE     SNMP ACL源地址限制
  SW-DHCP-01      high     S/CE     禁止dhcp enable
  SW-IPV6-01      high     S        IPv6流量策略阻断(五段链)
  SW-DHCP-02      high     S        DHCP流量策略阻断(五段链)
  SW-VLAN1-01     high     S/CE     VLAN1禁用
  SW-PS-01        high     S        端口安全(port-security)
  SW-PS-02        high     CE       MAC数量限制(mac-limit)
  ─────────────────────────────────────────────────


十二、注意事项
------------------------------------------------------------

1. 凭证安全:
   devices.csv 含明文口令, 运行完成后请删除或移至安全位置
   ★ 更安全的方式: password列留空, 用环境变量 VRP_PASS_<IP> 传递
   snapshots/ 含设备全量配置, 属敏感资产, 用完清理或归档

2. Telnet交互:
   程序自动适配AAA模式和密码认证模式
   设备sysname需只含letter和下划线(如 SW_01, Core_02)
   设备banner不得包含冒号 ':' 字符
   ★ 使用runtime(自带Python 3.12)时, telnetlib可用
     使用系统Python >= 3.13时, telnetlib已移除, 仅离线模式可用

3. CE系列注意:
   CE系列整改命令末尾需要 commit, fix模板已包含
   CE系列无 port-security 命令, 对应规则为 mac-limit(SW-PS-02)

4. 离线复检:
   在线运行会自动将每台设备的配置存为 snapshots/<IP>.cfg
   之后可用 ./run.sh --offline 反复跑, 不再登录设备

5. Web服务:
   Web服务依赖(fastapi/uvicorn/pydantic)已内置在vendor/
   启动后通过浏览器操作全部功能, 无需SSH/终端
   API文档: http://localhost:8080/docs

6. UOS系统提示:
   若执行时报 Permission denied, 说明UOS安全中心拦截了非商店程序
   解决: 设置 → 通用 → 开发者模式 → 开启
   或用 sudo 执行: sudo ./run.sh


十三、故障排查
------------------------------------------------------------

问题: --selfcheck 报 "ModuleNotFoundError"
原因: vendor目录缺失或不完整
解决: 确认 vendor/ 下有 yaml/ openpyxl/ fastapi/ 等目录

问题: telnetlib不可用
原因: Python 3.13+ 移除了 telnetlib
解决: 使用 runtime/ 下的 Python 3.12 (自带telnetlib)
      若无runtime, 仅使用 --offline 离线模式

问题: pydantic_core 导入失败 / Illegal instruction
原因: C扩展与目标平台架构不匹配
解决: 确认 vendor/pydantic_core/ 中的 .so 文件是
      _pydantic_core.cpython-312-aarch64-linux-gnu.so
      若不是, 在ARM64 Linux上执行:
      pip download pydantic-core==2.50.1 --platform manylinux2014_aarch64 \
        --python-version 312 --only-binary :all: -d /tmp/pyc
      解压 whl, 将 .so 文件放入 vendor/pydantic_core/

问题: runtime/bin/python3.12 报 "No such file or directory"
原因: 文件权限或架构不匹配
解决: chmod +x runtime/bin/python3.12
      确认目标机器是 aarch64 (uname -m)

问题: 登录超时或认证失败
原因: 设备认证模式不匹配或设备名含非法字符
解决: 确认设备sysname只含letter和下划线; 确认banner不含冒号

问题: 某条规则全部FAIL但设备实际合规
原因: 不同VRP版本的配置回显格式差异
解决: 把 snapshots/<IP>.cfg 中相关段落发给开发者修正 rules.yaml

问题: 报告中文乱码
原因: 终端编码非UTF-8
解决: export LANG=zh_CN.UTF-8 后重新执行

============================================================
  版本: 2.2
  运行时: 自带 Python 3.12 aarch64 Linux (runtime/)
  依赖: 全部内置在 vendor/ (无需pip install)
  适用: 华为S系列/CE系列园区交换机 | 协议: Telnet
  CLI: ./run.sh | Web: ./run_web.sh | 打包: ./pack.sh
  Docker: docker compose up -d | 镜像构建: docker build -t vrp-baseline .
============================================================


十四、Docker容器化部署
------------------------------------------------------------

★ 容器化优势:
  - 开发/测试/生产环境完全一致
  - 无需 runtime/ 和 vendor/, 镜像内自带 Python 3.12 + 全部依赖
  - 自动适配 x86_64 和 aarch64 (multi-arch)
  - 用户数据通过数据卷挂载, 与镜像解耦
  - 容器以非 root (uid 1000) 运行; data/ 目录需对该 uid 可写

1. 构建镜像:
     docker build -t vrp-baseline:2.2 .

2. 准备数据目录:
     mkdir -p data/snapshots data/output
     cp devices.csv  data/
     cp profile.yaml data/
     cp rules.yaml   data/
     # 离线测试: 文件名必须是 devices.csv 中的设备 IP
     cp snapshots/10.10.1.11.cfg data/snapshots/10.10.1.11.cfg
     # snapshots/sample.cfg 是与该文件内容一致的示意样例, 不会直接被读取

3. 启动Web服务 (推荐):
     docker compose up -d

   访问:
     http://<宿主机IP>:8080        前端页面
     http://<宿主机IP>:8080/docs   API文档

   查看日志:
     docker compose logs -f

   停止:
     docker compose down

4. CLI模式 (一次性运行):
     # 环境自检:
     docker compose run --rm cli --selfcheck

     # 离线检查:
     docker compose run --rm cli --offline

     # 在线检查(Telnet):
     docker compose run --rm cli

5. 设备密码安全:
   推荐通过环境变量传递密码, 不写入 devices.csv:

     docker compose run --rm \
       -e VRP_PASS_10_10_1_11=your_password \
       cli

   或在 docker-compose.yml 的 environment 中添加:
     environment:
       - VRP_PASS_10_10_1_11=your_password

6. 获取报告:
   报告自动输出到 ./data/output/ 目录:
     data/output/remediation_report.md
     data/output/baseline_report.xlsx

7. 重新构建 (代码更新后):
     docker compose build --no-cache
     docker compose up -d

8. Docker文件清单:
   ─────────────────────────────────────────
   Dockerfile              镜像构建定义
   docker-compose.yml      编排文件(Web+CLI)
   docker-entrypoint.sh    容器入口脚本
   .dockerignore           构建排除列表
   requirements.txt        Python依赖锁定
   ─────────────────────────────────────────

★ 三种部署方式对比:
  ┌─────────────┬──────────────┬──────────────────┐
  │ 方式        │ 适用场景     │ Python环境       │
  ├─────────────┼──────────────┼──────────────────┤
  │ tar.gz绿色版│ 无Docker环境 │ 自带runtime      │
  │             │ 需解压+chmod │ Python 3.12 ARM  │
  ├─────────────┼──────────────┼──────────────────┤
  │ Docker容器 │ 已装docker-ce│ 镜像内Python 3.12 │
  │             │ 开发+生产一致 │ multi-arch自动   │
  ├─────────────┼──────────────┼──────────────────┤
  │ 系统Python  │ 已有Python3  │ 需pip install    │
  │             │ 临时调试     │ 需3.12+有telnet  │
  └─────────────┴──────────────┴──────────────────┘
