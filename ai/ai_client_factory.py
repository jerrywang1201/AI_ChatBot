# ai/ai_client_factory.py
from interlinked import AI
from interlinked.core.clients.googleaiclient import GoogleAIClient
import os

def get_ai_client():
    model = os.getenv("INTERLINKED_MODEL", "gemini-2.5-flash")
    api_key = os.getenv("INTERLINKED_API_KEY") 
    AI.client = GoogleAIClient(model_name=model, api_key=api_key)
    return AI