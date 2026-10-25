FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core espeak-ng nodejs npm && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements-story.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-story.txt
COPY requirements-cpu.txt .
RUN pip install --no-cache-dir 'torch>=2.6,<3' --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir -r requirements-cpu.txt
RUN npm install --ignore-scripts --no-audit --no-fund @magenta/music@1.23.1
COPY . .
CMD ["python", "workflow.py", "preflight"]
