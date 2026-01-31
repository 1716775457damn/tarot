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
import hmac
import base64
import json
import time
import websockets
import asyncio

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件服务（CSS、JS、图片等）
# 注意：需要在 tarotobot_site 目录下运行，这样路径才正确
import pathlib
BASE_DIR = pathlib.Path(__file__).parent
app.mount("/includes", StaticFiles(directory=str(BASE_DIR / "includes")), name="includes")

# 让 /prediction/17/index.html 这种路径能直接被访问（避免 404 黑屏）
app.mount("/prediction", StaticFiles(directory=str(BASE_DIR / "prediction"), html=True), name="prediction")

@app.get("/")
async def read_root():
    return FileResponse(str(BASE_DIR / "index.html"))

@app.get("/favicon.ico")
async def favicon():
    # 站点不强依赖 favicon，避免刷一堆 404
    return Response(status_code=204)

# 配置 Kimi (Moonshot) API
MOONSHOT_API_KEY = "sk-AKTZlHsYOx7qyJtcNWWlkaTMspOua9c5LUCmfPSLVaXDuA1S"

client = OpenAI(
    api_key=MOONSHOT_API_KEY,
    base_url="https://api.moonshot.cn/v1",
)

# 配置百度翻译 API
BAIDU_APP_ID = "1Gn7_d5ss14k7jonrr0o6ola0"
BAIDU_APP_KEY = "1Gn7_d5ss14k7jonrr0o6ola0"  # 如果APP_KEY不同，请修改这里

# 配置讯飞语音转文字 API
IFLYTEK_APP_ID = "d012b168"
IFLYTEK_API_KEY = "25b522ce759aaad755cc9b3883b884ea"
IFLYTEK_API_SECRET = "MTY0YTQ5MTdkNmVkYTdmMGZmNjY5OTA5"


def baidu_translate(text: str, from_lang: str = "en", to_lang: str = "zh") -> str:
    """
    使用百度翻译将文本翻译成中文。
    如果没有配置 BAIDU_APP_ID / BAIDU_APP_KEY，则直接返回原文，保证服务可用。
    """
    if not text:
        return text
    if not BAIDU_APP_ID or not BAIDU_APP_KEY:
        # 未配置百度翻译密钥时，直接返回英文
        return text

    endpoint = "https://fanyi-api.baidu.com/api/trans/vip/translate"
    salt = str(random.randint(32768, 65536))
    sign_str = BAIDU_APP_ID + text + salt + BAIDU_APP_KEY
    sign = hashlib.md5(sign_str.encode("utf-8")).hexdigest()

    params = {
        "q": text,
        "from": from_lang,
        "to": to_lang,
        "appid": BAIDU_APP_ID,
        "salt": salt,
        "sign": sign,
    }

    try:
        resp = requests.get(endpoint, params=params, timeout=5)
        data = resp.json()
        if "trans_result" in data:
            # 可能会有多段，拼接起来
            return "".join(item.get("dst", "") for item in data["trans_result"])
    except Exception:
        # 任何错误都退回原文，避免影响主流程
        pass
    return text

class FortuneRequest(BaseModel):
    name: str
    birth_date: str # "1995-08-20 14:30"
    question: str   # "今天穿什么颜色"
    cards: list     # ["愚人", "宝剑三", "太阳"] (前端传来的牌)


@app.get("/api/precards")
async def get_prepared_cards():
    """
    预抽 3 张塔罗牌，并用百度翻译把牌名 / 正位含义翻译成中文。
    前端会在用户录入完个人信息后调用这个接口。
    """
    try:
        r = requests.get("https://tarotapi.dev/api/v1/cards/random?n=3", timeout=5)
        r.raise_for_status()
        data = r.json()
        raw_cards = (data.get("cards") or [])[:3]
    except Exception:
        # 如果外部 tarotapi.dev 不可用，就返回空数组，让前端走本地回退逻辑
        return {"cards": []}

    result = []
    for c in raw_cards:
        name_en = c.get("name", "")
        meaning_up_en = c.get("meaning_up", "")
        result.append(
            {
                "name_en": name_en,
                "name_zh": baidu_translate(name_en, from_lang="en", to_lang="zh"),
                "meaning_up_en": meaning_up_en,
                "meaning_up_zh": baidu_translate(
                    meaning_up_en, from_lang="en", to_lang="zh"
                ),
            }
        )

    return {"cards": result}

@app.post("/api/divine")
async def calculate_fortune(req: FortuneRequest):
    from fastapi.responses import JSONResponse
    
    try:
        def _safe_get_name(x):
            # 兼容 lunar_python 不同版本：可能返回对象（有 getName）或直接返回 str
            return x.getName() if hasattr(x, "getName") else str(x)

        # --- 第一步：使用开源算法算八字 ---
        # 解析日期
        try:
            year = int(req.birth_date[:4])
            month = int(req.birth_date[5:7])
            day = int(req.birth_date[8:10])
            hour = int(req.birth_date[11:13]) if len(req.birth_date) > 13 else 12
        except (ValueError, IndexError) as e:
            return JSONResponse(
                status_code=400,
                content={"error": f"日期格式错误: {str(e)}", "reply": "请检查出生日期格式是否正确"}
            )
        
        # 调用 lunar-python
        try:
            solar = Solar.fromYmdHms(year, month, day, hour, 0, 0)
            lunar = solar.getLunar()
            bazi = lunar.getEightChar()
            
            # 获取核心命理参数
            day_gan = bazi.getDayGan()
            day_master = _safe_get_name(day_gan)  # 日主（例如：甲木）
            wuxing_obj = day_gan.getWuXing() if hasattr(day_gan, "getWuXing") else None
            wuxing = _safe_get_name(wuxing_obj) if wuxing_obj is not None else ""
            current_lunar_date = f"{lunar.getMonthInChinese()}月{lunar.getDayInChinese()}"
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"error": f"八字计算失败: {str(e)}", "reply": "命理计算出现错误，请重试"}
            )

        # --- 第二步：构建 AI Prompt ---
        system_prompt = """
        你是一位精通《周易》、八字命理与西方塔罗的玄学大师。
        你需要结合用户的【八字五行】和抽到的【塔罗牌】，回答用户的具体问题。
        回答风格：神秘、优美、但在建议上要具体实用。
        **重要：回答必须简洁，控制在150字以内，直接给出核心建议。**
        """
        
        user_prompt = f"""
        【用户信息】
        - 姓名：{req.name}
        - 八字日主：{day_master} (五行属{wuxing})
        - 农历日期：{current_lunar_date}
        
        【用户问题】
        "{req.question}"
        
        【抽牌结果】
        1. 过去/根源：{req.cards[0] if len(req.cards) > 0 else '未知'}
        2. 现在/现状：{req.cards[1] if len(req.cards) > 1 else '未知'}
        3. 未来/建议：{req.cards[2] if len(req.cards) > 2 else '未知'}
        
        【推演要求】
        1. 简洁分析八字五行与今日运势的关系（1-2句）。
        2. 结合塔罗牌义解释问题（1-2句）。
        3. 给出直接的建议（比如颜色、方位、时间），控制在50字以内。
        4. **总字数不超过150字，避免冗长描述。**
        """

        # --- 第三步：调用 Kimi 模型 ---
        try:
            if not MOONSHOT_API_KEY:
                return JSONResponse(
                    status_code=500,
                    content={"error": "API密钥未配置", "reply": "系统配置错误，请联系管理员"}
                )
            
            completion = client.chat.completions.create(
                model="moonshot-v1-8k",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
            )
            
            result_text = completion.choices[0].message.content
        except Exception as e:
            import traceback
            error_detail = str(e)
            print(f"Kimi API 调用失败: {error_detail}")
            print(traceback.format_exc())
            return JSONResponse(
                status_code=500,
                content={
                    "error": f"AI模型调用失败: {error_detail}",
                    "reply": "AI占卜服务暂时不可用，请稍后重试。如果问题持续，请检查API密钥配置。"
                }
            )

        return {
            "bazi_summary": f"{day_master}命，五行属{wuxing}",
            "reply": result_text
        }
    except Exception as e:
        import traceback
        print(f"未知错误: {str(e)}")
        print(traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"error": f"服务器内部错误: {str(e)}", "reply": "系统出现未知错误，请重试"}
        )


def generate_iflytek_auth_url():
    """
    生成讯飞 WebSocket 认证 URL（带签名）。
    参考：https://www.xfyun.cn/doc/asr/voicedictation/API.html
    """
    host = "iat-api.xfyun.cn"
    path = "/v2/iat"
    
    # 生成 RFC1123 格式的时间戳
    now = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())
    
    # 构建签名字符串
    signature_origin = f"host: {host}\ndate: {now}\nGET {path} HTTP/1.1"
    
    # 使用 APISecret 进行 HMAC-SHA256 签名
    signature_sha = hmac.new(
        IFLYTEK_API_SECRET.encode('utf-8'),
        signature_origin.encode('utf-8'),
        hashlib.sha256
    ).digest()
    signature = base64.b64encode(signature_sha).decode('utf-8')
    
    # 构建 authorization 字符串
    authorization_origin = f'api_key="{IFLYTEK_API_KEY}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
    authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode('utf-8')
    
    # 构建完整 URL
    url = f"wss://{host}{path}?authorization={authorization}&date={now}&host={host}"
    return url


@app.websocket("/ws/asr")
async def websocket_asr_proxy(websocket: WebSocket):
    """
    WebSocket 代理：前端发送音频数据，后端转发到讯飞 API，返回转写结果。
    """
    await websocket.accept()

    if not IFLYTEK_APP_ID or not IFLYTEK_API_KEY or not IFLYTEK_API_SECRET:
        await websocket.send_json({"type": "error", "message": "讯飞语音配置缺失：请设置 IFLYTEK_APP_ID / IFLYTEK_API_KEY / IFLYTEK_API_SECRET"})
        await websocket.close()
        return
    
    iflytek_ws = None
    try:
        # 连接到讯飞 WebSocket
        auth_url = generate_iflytek_auth_url()
        iflytek_ws = await websockets.connect(auth_url)
        
        # 发送初始参数
        params = {
            "common": {
                "app_id": IFLYTEK_APP_ID
            },
            "business": {
                "language": "zh_cn",
                "domain": "iat",
                "accent": "mandarin",
                "vad_eos": 10000  # 静音检测，10秒后自动结束
            },
            "data": {
                "status": 0,  # 0: 第一帧，1: 中间帧，2: 最后一帧
                "format": "audio/L16;rate=16000",  # 16kHz 采样率，PCM
                "encoding": "raw",
                "audio": ""
            }
        }
        
        # 发送第一帧（空音频，用于初始化）
        await iflytek_ws.send(json.dumps(params))
        
        async def forward_to_iflytek():
            """从客户端接收音频数据，转发到讯飞"""
            frame_count = 0
            try:
                while True:
                    try:
                        data = await websocket.receive_bytes()
                        frame_count += 1
                        
                        # 将音频数据转为 base64
                        audio_base64 = base64.b64encode(data).decode('utf-8')
                        
                        # 构建发送参数（中间帧）
                        send_params = {
                            "data": {
                                "status": 1,  # 中间帧
                                "format": "audio/L16;rate=16000",
                                "encoding": "raw",
                                "audio": audio_base64
                            }
                        }
                        
                        await iflytek_ws.send(json.dumps(send_params))
                    except WebSocketDisconnect:
                        # 客户端断开，发送最后一帧（空音频）
                        final_params = {
                            "data": {
                                "status": 2,  # 最后一帧
                                "format": "audio/L16;rate=16000",
                                "encoding": "raw",
                                "audio": ""
                            }
                        }
                        await iflytek_ws.send(json.dumps(final_params))
                        break
                    except Exception as e:
                        await websocket.send_json({"type": "error", "message": f"转发音频失败: {str(e)}"})
                        break
            except Exception as e:
                await websocket.send_json({"type": "error", "message": f"音频处理错误: {str(e)}"})
        
        async def forward_to_client():
            """从讯飞接收转写结果，转发到客户端"""
            while True:
                try:
                    result = await iflytek_ws.recv()
                    result_data = json.loads(result)
                    
                    # 解析讯飞返回的数据
                    if result_data.get("code") == 0:
                        if "data" in result_data:
                            data = result_data["data"]
                            if "result" in data:
                                # 提取转写文本
                                ws_result = data["result"]
                                text = ""
                                if "ws" in ws_result:
                                    for ws_item in ws_result["ws"]:
                                        for cw in ws_item.get("cw", []):
                                            text += cw.get("w", "")
                                
                                if text:
                                    await websocket.send_json({
                                        "type": "result",
                                        "text": text,
                                        "is_final": data.get("status") == 2
                                    })
                    else:
                        await websocket.send_json({
                            "type": "error",
                            "message": result_data.get("message", "识别失败")
                        })
                    
                    # 如果状态为2，表示识别完成
                    if result_data.get("data", {}).get("status") == 2:
                        await websocket.send_json({"type": "done"})
                        break
                except websockets.exceptions.ConnectionClosed:
                    break
                except Exception as e:
                    await websocket.send_json({"type": "error", "message": str(e)})
                    break
        
        # 并发执行转发任务
        await asyncio.gather(
            forward_to_iflytek(),
            forward_to_client(),
            return_exceptions=True
        )
        
    except Exception as e:
        await websocket.send_json({"type": "error", "message": f"连接讯飞失败: {str(e)}"})
    finally:
        if iflytek_ws:
            await iflytek_ws.close()
        try:
            await websocket.close()
        except:
            pass

# 启动命令: uvicorn server:app --reload --host 0.0.0.0 --port 8000
