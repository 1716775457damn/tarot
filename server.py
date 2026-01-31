from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from lunar_python import Solar
from openai import OpenAI
import os
import requests
import hashlib
import random
import json
import pathlib

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路径配置
BASE_DIR = pathlib.Path(__file__).parent
app.mount("/includes", StaticFiles(directory=str(BASE_DIR / "includes")), name="includes")
app.mount("/prediction", StaticFiles(directory=str(BASE_DIR / "prediction"), html=True), name="prediction")


# ==========================================
# 核心新增 1：WebSocket 连接管理器
# ==========================================
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"新网页连接，当前在线: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            print(f"网页断开，当前在线: {len(self.active_connections)}")

    async def broadcast(self, message: str):
        # 向所有连接的网页广播文本
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass


manager = ConnectionManager()


# ==========================================
# 核心新增 2：WebSocket 路由 (网页前端连接用)
# ==========================================
@app.websocket("/ws/text_input")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # 保持连接，接收心跳
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ==========================================
# 核心新增 3：文字接收接口 (供本地 Python 调用)
# ==========================================
class TextPayload(BaseModel):
    text: str


@app.post("/api/push_text")
async def push_voice_text(payload: TextPayload):
    """
    接收本地转写好的文字，推送到网页
    """
    print(f"收到本地推送的文字: {payload.text}")
    await manager.broadcast(payload.text)
    return {"status": "success", "broadcast_to": len(manager.active_connections)}


# ==========================================
# 原有业务逻辑 (Kimi, 塔罗牌等)
# ==========================================
MOONSHOT_API_KEY = "sk-AKTZlHsYOx7qyJtcNWWlkaTMspOua9c5LUCmfPSLVaXDuA1S"
client = OpenAI(api_key=MOONSHOT_API_KEY, base_url="https://api.moonshot.cn/v1")

BAIDU_APP_ID = "1Gn7_d5ss14k7jonrr0o6ola0"
BAIDU_APP_KEY = "1Gn7_d5ss14k7jonrr0o6ola0"


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
        # 八字计算
        year, month, day = int(req.birth_date[:4]), int(req.birth_date[5:7]), int(req.birth_date[8:10])
        solar = Solar.fromYmdHms(year, month, day, 12, 0, 0)
        lunar = solar.getLunar()
        bazi = lunar.getEightChar()
        day_master = bazi.getDayGan().getName()
        wuxing = bazi.getDayGan().getWuXing().getName()

        # Kimi 推理
        prompt = f"用户:{req.name},日主:{day_master}({wuxing}),问题:{req.question},牌:{req.cards}. 请用塔罗结合八字，150字内给出建议。"
        completion = client.chat.completions.create(
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