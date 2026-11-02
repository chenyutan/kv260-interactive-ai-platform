"""Downloads official model assets only. User videos never leave the machine."""
from pathlib import Path
import hashlib
import json
import urllib.request

ROOT=Path(__file__).resolve().parent
MODELS={
    "pose_landmarker_lite.task":"https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
    "hand_landmarker.task":"https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"}

def main():
    folder=ROOT/"models"; folder.mkdir(exist_ok=True); manifest={}
    for name,url in MODELS.items():
        target=folder/name
        if not target.exists():
            print("Downloading",name,flush=True)
            with urllib.request.urlopen(url,timeout=60) as response: content=response.read()
            if len(content)<100000: raise ValueError("Incomplete model download: "+name)
            temp=target.with_suffix(".partial"); temp.write_bytes(content); temp.replace(target)
        manifest[name]={"source":url,"sha256":hashlib.sha256(target.read_bytes()).hexdigest(),"bytes":target.stat().st_size}
    (folder/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print("Models ready.")

if __name__=="__main__": main()
