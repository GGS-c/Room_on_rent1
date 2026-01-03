import os
from google import genai
from dotenv import load_dotenv

load_dotenv()
client = genai.Client()
# The client automatically picks up the API key from the environment