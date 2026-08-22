# Hand-gesture control: MediaPipe needs Python <=3.12, which the host may not
# have (Ubuntu 26.04 ships 3.14), so it runs containerized with its own runtime.
FROM python:3.12-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
  && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
  mediapipe==0.10.21 opencv-python-headless websocket-client==1.8.0

COPY gesture_service.py /app/gesture_service.py

CMD ["python", "-u", "/app/gesture_service.py"]
