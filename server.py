import os
import uuid
import logging
import json
import pathlib
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from groq import Groq
from dotenv import load_dotenv
import edge_tts
from lunar_python import Solar
from openai import OpenAI
import requests
import hashlib
import random

# ================= 配置与初始化 =================
app = FastAPI()

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路径配置
BASE_DIR = pathlib.Path(__file__).parent
os.makedirs("static", exist_ok=True)  # 确保 static 目录存在用于存音频

# 挂载静态文件
app.mount("/includes", StaticFiles(directory=str(BASE_DIR / "includes")), name="includes")
app.mount("/prediction", StaticFiles(directory=str(BASE_DIR / "prediction"), html=True), name="prediction")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# 加载 API Key
load_dotenv()
groq_key = os.getenv("GROQ_API_KEY")
client = Groq(api_key=groq_key)

# 塔罗牌/Moonshot API 配置 (保留原样)
MOONSHOT_API_KEY = "sk-AKTZlHsYOx7qyJtcNWWlkaTMspOua9c5LUCmfPSLVaXDuA1S"  # 建议放入 .env
moonshot_client = OpenAI(api_key=MOONSHOT_API_KEY, base_url="https://api.moonshot.cn/v1")
BAIDU_APP_ID = "1Gn7_d5ss14k7jonrr0o6ola0"
BAIDU_APP_KEY = "1Gn7_d5ss14k7jonrr0o6ola0"


# ================= 核心：连接管理器 (桥接网页与本地) =================
class ConnectionManager:
    def __init__(self):
        self.browser_connections: list[WebSocket] = []  # 网页连接列表
        self.bridge_ws: WebSocket | None = None  # 本地电脑连接

    # --- 网页端管理 ---
    async def connect_browser(self, websocket: WebSocket):
        await websocket.accept()
        self.browser_connections.append(websocket)

    def disconnect_browser(self, websocket: WebSocket):
        if websocket in self.browser_connections:
            self.browser_connections.remove(websocket)

    async def broadcast_to_browsers(self, message: str):
        """发送文字给所有网页显示"""
        for connection in self.browser_connections:
            try:
                await connection.send_text(message)
            except:
                pass

    # --- 本地桥接端管理 ---
    async def connect_bridge(self, websocket: WebSocket):
        await websocket.accept()
        self.bridge_ws = websocket
        print("✅ 本地电脑(Bridge) 已连接到公网")

    def disconnect_bridge(self):
        self.bridge_ws = None
        print("❌ 本地电脑(Bridge) 断开")

    async def send_start_command_to_bridge(self):
        """网页点击 -> 发送指令给本地电脑"""
        if self.bridge_ws:
            try:
                # 发送指令给本地 server_local.py
                await self.bridge_ws.send_text("CMD:START_RECORD")
                return True
            except:
                return False
        return False


manager = ConnectionManager()


# ================= WebSocket 路由 =================

# 1. 网页前端连接这里 (index.html 调用)
@app.websocket("/ws/text_input")  # 保持和你前端代码一致的路径
async def ws_browser(websocket: WebSocket):
    await manager.connect_browser(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                # 如果网页点击了“开始” (需要前端发 {"command": "start"})
                if msg.get("command") == "start":
                    success = await manager.send_start_command_to_bridge()
                    if success:
                        await websocket.send_text("指令已发送，ESP32 正在启动录音...")
                    else:
                        await websocket.send_text("❌ 错误：本地设备未连接到服务器")
            except json.JSONDecodeError:
                pass  # 忽略非JSON消息
    except WebSocketDisconnect:
        manager.disconnect_browser(websocket)


# 2. 本地电脑连接这里 (server_local.py 调用)
@app.websocket("/ws/bridge")
async def ws_bridge(websocket: WebSocket):
    await manager.connect_bridge(websocket)
    try:
        while True:
            await websocket.receive_text()  # 保持心跳，防止断开
    except WebSocketDisconnect:
        manager.disconnect_bridge()


# ================= 业务 API =================

class TextInput(BaseModel):
    text: str


# 接收本地转写好的文字 -> 处理 LLM & TTS -> 返回结果给本地
@app.post("/api/process_input")
async def process_input(input_data: TextInput):
    user_text = input_data.text
    print(f"收到文字: {user_text}")

    # 1. 广播给网页显示 (让网页输入框自动填充)
    await manager.broadcast_to_browsers(user_text)

    # 2. LLM (Llama3 on Groq) 思考
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "你是一个中文语音助手。请用简短的中文回答（50字以内）。"},
                {"role": "user", "content": user_text}
            ],
            max_tokens=100
        )
        ai_text = completion.choices[0].message.content
    except Exception as e:
        ai_text = f"AI思考出错: {str(e)}"

    # 3. TTS 生成语音
    output_filename = f"reply_{uuid.uuid4().hex[:8]}.mp3"
    output_path = os.path.join("static", output_filename)

    try:
        communicate = edge_tts.Communicate(ai_text, "zh-CN-XiaoxiaoNeural")
        await communicate.save(output_path)
    except Exception as e:
        print(f"TTS生成失败: {e}")

    # 生成公网可访问的音频URL (请确保域名正确)
    audio_url = f"https://www.yunyingtec.com/static/{output_filename}"

    # 4. 返回给本地电脑 (以便本地电脑转发给 ESP32 播放)
    return {
        "status": "success",
        "ai_text": ai_text,
        "audio_url": audio_url
    }


# ================= 原有塔罗牌逻辑 (保持不变) =================

def baidu_translate(text: str, from_lang: str = "en", to_lang: str = "zh") -> str:
    if not text or not BAIDU_APP_ID: return text
    endpoint = "https://fanyi-api.baidu.com/api/trans/vip/translate"
    salt = str(random.randint(32768, 65536))
    sign = hashlib.md5((BAIDU_APP_ID + text + salt + BAIDU_APP_KEY).encode("utf-8")).hexdigest()
    try:
        resp = requests.get(endpoint,
                            params={"q": text, "from": from_lang, "to": to_lang, "appid": BAIDU_APP_ID, "salt": salt,
                                    "sign": sign}, timeout=5)
        data = resp.json()
        if "trans_result" in data: return "".join(item.get("dst", "") for item in data["trans_result"])
    except:
        pass
    return text


class FortuneRequest(BaseModel):
    name: str
    birth_date: str
    question: str
    cards: list


@app.get("/api/precards")
async def get_prepared_cards():
    try:
        r = requests.get("https://tarotapi.dev/api/v1/cards/random?n=3", timeout=5)
        raw_cards = (r.json().get("cards") or [])[:3]
    except:
        return {"cards": []}
    result = []
    for c in raw_cards:
        result.append({
            "name_en": c.get("name", ""),
            "name_zh": baidu_translate(c.get("name", ""), "en", "zh"),
            "meaning_up_en": c.get("meaning_up", ""),
            "meaning_up_zh": baidu_translate(c.get("meaning_up", ""), "en", "zh")
        })
    return {"cards": result}


@app.post("/api/divine")
async def calculate_fortune(req: FortuneRequest):
    from fastapi.responses import JSONResponse
    try:
        year, month, day = int(req.birth_date[:4]), int(req.birth_date[5:7]), int(req.birth_date[8:10])
        solar = Solar.fromYmdHms(year, month, day, 12, 0, 0)
        lunar = solar.getLunar()
        bazi = lunar.getEightChar()
        day_master = bazi.getDayGan().getName()
        wuxing = bazi.getDayGan().getWuXing().getName()

        prompt = f"用户:{req.name},日主:{day_master}({wuxing}),问题:{req.question},牌:{req.cards}. 请用塔罗结合八字，150字内给出建议。"
        completion = moonshot_client.chat.completions.create(
            model="moonshot-v1-8k",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        return {"bazi_summary": f"{day_master}命，五行属{wuxing}", "reply": completion.choices[0].message.content}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "reply": "AI算命太火爆了，请稍后再试"})


@app.get("/")
async def read_root():
    return FileResponse(str(BASE_DIR / "index.html"))


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)