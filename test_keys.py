import os
import asyncio
from dotenv import load_dotenv
import litellm

load_dotenv()

async def test_keys():
    print("Testing Groq API...")
    try:
        res1 = await litellm.acompletion(model=os.getenv("PRIMARY_MODEL_NAME"), messages=[{"role": "user", "content": "Hello"}])
        print(f"[SUCCESS] Groq: {res1.choices[0].message.content.strip()}")
    except Exception as e:
        print(f"[ERROR] Groq: {e}")

    print("\nTesting Gemini API...")
    try:
        res2 = await litellm.acompletion(model=os.getenv("SHADOW_MODEL_NAME"), messages=[{"role": "user", "content": "Hello"}])
        print(f"[SUCCESS] Gemini: {res2.choices[0].message.content.strip()}")
    except Exception as e:
        print(f"[ERROR] Gemini: {e}")

if __name__ == "__main__":
    asyncio.run(test_keys())
