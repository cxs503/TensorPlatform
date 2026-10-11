FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy backend
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY virtual-data/ ./virtual-data/

# Install Python dependencies
RUN pip install --no-cache-dir -r backend/requirements.txt

# Expose ports
EXPOSE 8000 3000

# Create startup script
RUN echo '#!/bin/bash\n\
echo "Starting foundation AI Platform..."\n\
cd /app/backend\n\
uvicorn main:app --host 0.0.0.0 --port 8000 &\n\
cd /app/frontend\n\
python3 -m http.server 3000 &\n\
wait' > /start.sh && chmod +x /start.sh

CMD ["/start.sh"]
