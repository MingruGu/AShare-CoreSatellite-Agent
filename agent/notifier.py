import requests


class WeChatNotifier:
    """Server酱微信服务号消息推送器"""

    def __init__(self, sendkey: str = ""):
        self.sendkey = sendkey.strip()

    def send(self, title: str, content: str) -> bool:
        if not self.sendkey:
            return False
        url = f"https://sctapi.ftqq.com/{self.sendkey}.send"
        payload = {
            "title": title,
            "desp": content.replace("\n", "\n\n"),
        }
        try:
            resp = requests.post(url, data=payload, timeout=10).json()
            if resp.get("code") == 0:
                print("📲 微信提醒已成功推送！")
                return True
            print(f"⚠️ 微信推送返回异常: {resp}")
            return False
        except Exception as e:
            print(f"⚠️ 微信推送请求失败: {e}")
            return False
