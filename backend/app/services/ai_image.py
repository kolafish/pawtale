import httpx
import base64
from app.config import get_settings

settings = get_settings()


class AIImageService:
    """GPT-4o 图像生成服务"""

    def __init__(self):
        self.api_key = settings.openai_api_key

    async def generate_avatar(
        self,
        character: dict,
        style: str = "日漫",
        reference_image: str | None = None,
    ) -> dict:
        """生成角色头像"""

        # 画风映射
        style_prompts = {
            "写实": "realistic portrait, photorealistic, detailed skin texture, natural lighting",
            "日漫": "anime style, Japanese animation, clean lines, vibrant colors, expressive eyes",
            "国漫": "Chinese animation style, elegant, flowing lines, traditional aesthetics",
            "水彩": "watercolor painting style, soft edges, artistic, dreamy atmosphere",
        }

        # 构建外貌描述
        appearance = character.get("appearance", "")
        if not appearance:
            appearance = "attractive young person"

        # 性格影响表情
        personality = character.get("personality", [])
        expression = "neutral expression"
        if "高冷" in personality:
            expression = "cool and aloof expression"
        elif "温柔" in personality:
            expression = "gentle and warm smile"
        elif "傲娇" in personality:
            expression = "slightly tsundere expression, looking away"
        elif "阳光" in personality:
            expression = "bright cheerful smile"

        prompt = f"""Portrait of a character: {appearance}
Expression: {expression}
Style: {style_prompts.get(style, style_prompts['日漫'])}
High quality, detailed, character portrait, upper body, clean background"""

        async with httpx.AsyncClient() as client:
            request_body = {
                "model": "gpt-image-1",
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
                "quality": "high",
            }

            # 如果有参考图，使用图生图
            if reference_image:
                request_body["image"] = reference_image

            response = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
                timeout=120.0,
            )
            response.raise_for_status()
            result = response.json()

        image_url = result["data"][0]["url"]

        return {
            "avatar_url": image_url,
            "base_image_url": image_url,  # 首次生成时，基准图就是头像
        }


ai_image_service = AIImageService()
