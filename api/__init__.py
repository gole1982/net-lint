"""
api — 华为VRP基线检查工具 FastAPI 接口层
================================================================
本包是 Web API 层, 调用 core/ 中的纯业务逻辑。
CLI (cli.py) 和 Web (api/) 共享同一个 core/ 引擎。

模块说明:
  schemas       Pydantic 请求/响应模型
  store         内存存储(设备列表/任务状态/报告结果)
  task_manager  后台异步任务管理
  app           FastAPI 应用入口
  routes/       路由模块
    devices.py    设备管理 CRUD
    profile.py    规则开关与变量管理
    tasks.py      检查任务管理
    system.py     系统管理

启动:
  uvicorn api.app:app --host 0.0.0.0 --port 8080 --reload
================================================================
"""
