import uvicorn
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List
from concurrent.futures import ThreadPoolExecutor

# 导入核心逻辑
from core_adb_logic import AdbCore, AdbException, ConfigValidator

# ========================================
# 1. 配置与初始化
# ========================================

app = FastAPI(title="Adayo Mega TestTool API", version="1.0")
adb_core = AdbCore()
# 线程池用于执行阻塞的 ADB 操作
executor = ThreadPoolExecutor(max_workers=5)

# WebSocket 连接管理器
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_log(self, source: str, message: str, tag: str = "INFO"):
        """异步发送日志到所有连接的 WebSocket"""
        log_entry = {
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "source": source,
            "message": message,
            "tag": tag
        }
        json_data = json.dumps(log_entry, ensure_ascii=False)

        # 广播日志
        for connection in self.active_connections:
            try:
                await connection.send_text(json_data)
            except RuntimeError:
                # 忽略连接关闭导致的错误
                pass

manager = ConnectionManager()
# 设置 AdbCore 的日志回调
adb_core.set_log_callback(manager.send_log)

# ========================================
# 2. Pydantic 模型
# ========================================
class ConfigPushData(BaseModel):
    new_pno: str
    new_vin: str

# ========================================
# 3. 实时通信 (WebSocket)
# ========================================
@app.websocket("/ws/log")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # 保持连接开启，等待日志推送
        while True:
            # 监听客户端发送的消息（可选，如果需要客户端控制流）
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        adb_core._log("WebSocket", "客户端已断开连接", "WARNING")

# ========================================
# 4. RESTful API 接口
# ========================================

def run_blocking_task(func, *args, **kwargs):
    """在线程池中运行阻塞的 ADB/文件操作"""
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(executor, func, *args, **kwargs)

@app.get("/device/status")
async def get_device_status():
    """获取设备连接状态"""
    try:
        status = await run_blocking_task(adb_core.get_device_status)
        logcat_count = await run_blocking_task(adb_core.count_remote_logcat)
        status['logcat_count'] = logcat_count
        return status
    except AdbException as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/config/pull")
async def pull_config():
    """拉取配置并返回本地 JSON 数据"""
    try:
        config = await run_blocking_task(adb_core.pull_config)
        return config
    except AdbException as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/config/push")
async def push_config(data: ConfigPushData):
    """推送配置（将 JSON 转换为 TXT 后推送）"""
    try:
        # 再次进行验证，确保数据安全
        if not ConfigValidator.validate_icc_pno(data.new_pno)[0] or not ConfigValidator.validate_vin(data.new_vin)[0]:
             raise HTTPException(status_code=400, detail="PNO 或 VIN 格式验证失败。")

        config = await run_blocking_task(adb_core.push_config, data.new_pno, data.new_vin)
        await manager.send_log("系统", "配置推送任务完成，设备已更新。", "SUCCESS")
        return {"status": "success", "config": config}
    except AdbException as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tool/reboot")
async def reboot():
    """重启设备"""
    try:
        await run_blocking_task(adb_core.reboot_device)
        return {"status": "success", "message": "设备重启命令已发送。"}
    except AdbException as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tool/clear-logcat")
async def clear_logcat():
    """清理远程 Logcat"""
    try:
        await run_blocking_task(adb_core.clear_remote_logcat)
        # 清理完成后，前端会再次调用 /device/status 更新计数
        return {"status": "success", "message": "远程 Logcat 已清理。"}
    except AdbException as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/log/pull")
async def start_log_pull(logs: List[str], export_folder: str):
    """启动日志拉取任务"""
    # 实际日志拉取可能需要一个单独的线程来管理进度，并实时通过 WebSocket 回传。
    # 为了简化，这里仅模拟异步任务启动：
    try:
        await adb_core.start_pull_process(logs, export_folder)
        return {"status": "started", "message": "日志拉取任务已启动。"}
    except AdbException as e:
        raise HTTPException(status_code=500, detail=str(e))

# ========================================
# 5. 启动入口
# ========================================
if __name__ == "__main__":
    # 在 8000 端口启动 API 服务
    uvicorn.run(app, host="127.0.0.1", port=8000)