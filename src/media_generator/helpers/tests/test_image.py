# test_image_api.py
import requests, base64
r = requests.post("http://127.0.0.1:7860/sdapi/v1/txt2img",
                  json={"prompt":"renaissance oil painting, 16:9","width":1024,"height":576})
img = base64.b64decode(r.json()["images"][0])
open("test.png","wb").write(img)

# on windows
# curl.exe -X POST http://127.0.0.1:7860/sdapi/v1/txt2img ^
#  -H "Content-Type: application/json" ^
#  -d "{\"prompt\":\"renaissance oil painting, 16:9\",\"width\":768,\"height\":432}"