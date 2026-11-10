FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core espeak-ng && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements-story.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-story.txt
COPY requirements-cpu.txt .
RUN pip install --no-cache-dir 'torch==2.7.1' --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir -r requirements-cpu.txt
COPY . .
CMD ["python", "fluency_video.py", "--help"]
