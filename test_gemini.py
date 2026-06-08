import os
from dotenv import load_dotenv
load_dotenv()
from google import genai

# print("env api key: ", os.environ['GOOGLE_API_KEY'])

client = genai.Client(
    project=os.environ['GOOGLE_CLOUD_PROJECT'],
    location=os.environ['GOOGLE_CLOUD_LOCATION'],
)
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="What is the capital of France?",
)
print(response.text)
